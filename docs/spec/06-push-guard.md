# 06 Push guard for every repo

Wave 3. Branch `spec/06-push-guard`. Test file `tests/test_push_guard.py`.

## Goal

Today the secret check guards only the store. With gitpro's global hooks in place, llmpro can guard every
push from every repo on the machine: gitpro's `pre-push` hook runs `<chain_to>/pre-push`, and llmpro puts
its own script there. The script checks the lines a push adds for known secret formats and for the
secret values found in the assistants' raw logs (the leak check), and stops the push on a hit. llmpro
adds the `chain_to` line to gitpro's config itself (DECISIONS I5, changed 2026-10-20).

## May edit

`src/llmpro/plugins/secrets.py`, `src/llmpro/hot.py`, `src/llmpro/check.py`.

## Creates

`src/llmpro/pushguard.py`, `tests/test_push_guard.py`.

## Uses

From 03:

```python
from llmpro import gitpro
gitpro.get().has('guard'); gitpro.get().run_json(['guard', 'status', '--json'])  # -> (code, obj)
from llmpro.plugins.base import SetupItem, Context
from llmpro.setup import Item, Undo, SetupError, tilde
```

From today's `check.py`: `scan_patterns(text) -> [(kind, value, offset)]`, `fp(v)`, `mask(v)`,
`line_of(text, offset)`, `part2_cached(...)`, `cache_dir(store)`, `load_cache(cdir)`, the allowlist loading
in `run()`, and `data_file(store, 'allowlist.json', 'secretcheck_allowlist.json')` from `store.py`.

Tests use `fake_gitpro`, `home`, `store`, `machine`, `onpath`, and real `git` in temporary repos (with a
bare repo as the remote).

## Requirements

### The check as a function (`check.py`)

1. Add `scan_texts(store, texts: dict[str, str], jobs=1) -> list[Hit]`, where `Hit` is a small dataclass
   `(label, offset, kind, masked, how)`; `how` is `pattern`, `whole` or `piece`. It runs part 1 over the
   texts and the cached part 2 with the store's raw sources, cache and allowlist, and prints nothing.
   Values and pieces in the allowlist are skipped as in `run()`.
2. Refactor only as far as needed so `part1`/`part2_cached` can hand back their hits as data. `check.run`
   and `llmpro check` output stay exactly as before; the existing tests in `tests/` are the proof.
3. `scan_texts` never writes the blob cache for the texts it was given (they are not store files).

### The guard (`pushguard.py`, `hot.py`)

4. `hot.py` gains `llmpro hook push-guard [remote] [url]`, dispatched in `hot.main` like the other hooks
   and kept as lean (it imports `pushguard`, which imports `check` only when there is something to scan).
5. `pushguard.run(argv, stdin) -> int`:
   - `LLMPRO_SKIP` set and not empty: exit 0 (the script also checks this).
   - Find the store (`store.locate(None)`); no store, or no `config.toml` there: exit 0 silently.
   - The repo is `git rev-parse --show-toplevel`; when it is the store itself: exit 0 (the store has its
     own hooks).
   - Read the stdin lines `<local ref> <local sha> <remote ref> <remote sha>`. Skip a line whose local
     sha is all zeros (a delete). For a remote sha of all zeros (a new branch) the commits are
     `git rev-list <local sha> --not --remotes`; otherwise `git rev-list <remote sha>..<local sha>`.
     At most 1000 commits per push; over that, print one line saying so and check the newest 1000.
   - For each commit, the added lines: `git log -p --no-color --no-ext-diff --format=%H -1 <sha>` (or
     one `git log` over all of them, your choice), keeping the `+` lines (not `+++`) of each file. One
     text per `(commit, path)`; its label is `<short sha> <path>`. Map each hit back to the line number
     in the new file using the hunk headers.
   - Binary files and files over 1 MB are skipped.
6. On hits: print to stderr `llmpro: push stopped, secrets in what this push adds:` then one line per
   hit, `  <short sha> <path>:<line> [<kind>] <masked>` (never the value; `mask` only), then
   `To push anyway: LLMPRO_SKIP=1 git push. A false positive goes into the allowlist: llmpro check --help.`
   Exit 1.
7. No hits: print nothing, exit 0.
8. Any internal error (git fails, the cache is broken, an exception): print one line
   `llmpro: push guard could not run: <reason>; the push goes on` to stderr and exit 0. The guard fails
   open; gitpro's own guard and the store's hooks are unaffected.
9. The whole check of a push of a few commits in a repo of normal size takes well under 2 s when the
   leak cache is fresh. Do not collect raw sources again when the cache is fresh (that is what
   `part2_cached` already does).

### The script and the setup item (`plugins/secrets.py`)

10. The script lives at `$XDG_DATA_HOME/llmpro/hooks/pre-push` (default `~/.local/share/llmpro/hooks/`),
    mode 0755:

    ```sh
    #!/bin/sh
    # llmpro push guard; gitpro's pre-push hook runs this through [guard] chain_to
    [ -n "${LLMPRO_SKIP:-}" ] && exit 0
    command -v llmpro >/dev/null 2>&1 || exit 0
    exec llmpro hook push-guard "$@"
    ```

    Export `pushguard.SCRIPT` (that text) and `pushguard.hooks_dir()` (that folder).
11. `secrets` plugin `setup_items` returns `[PushGuard()]` when gitpro has the capability `guard`, else
    `[]`. `PushGuard.survey` asks `gitpro guard status --json` and decides:
    - script missing or different: missing; apply writes it with mode 0755 in a folder made 0755 (undo
      removes it, or restores the earlier text if there was one). A hook folder that group or others can
      write is made private (`chmod go-w`), since gitpro refuses such a `chain_to` folder.
    - guard `installed` is `none`: 05's item runs gitpro setup in the same run, which installs it; the
      item goes on as for `chain_to` null.
    - `chain_to` null: missing. Apply copies gitpro's config to `<config>.before-llmpro`, adds

      ```toml
      [guard]
      chain_to = "~/.local/share/llmpro/hooks"
      ```

      (the line goes right under a lone `[guard]` header when there is one), keeps the file's mode, and
      runs `gitpro setup -y --agent none` so the hooks carry the new chain. The undo script puts the copy
      back and runs gitpro setup again. When the config does not exist, is not valid TOML, has several
      `[guard]` headers or a `guard` that is not a table, llmpro leaves it, and setup prints the lines for
      the user to add by hand and says to run `llmpro setup` again.
    - `chain_to` is our folder (compare after `~` expansion and `realpath`): present, state `in place`.
    - `chain_to` is another folder: present but informing; state `chain_to is <path>`; detail
      `gitpro runs that folder's pre-push, not llmpro's; to use both, call llmpro hook push-guard from it`.
12. `remove` takes out the script (undo puts it back). When the config holds the exact line llmpro writes,
    it takes that line out (with its `[guard]` header when the table held nothing else, so a file setup
    appended to comes back as it was), after a copy, and runs gitpro setup again; a `chain_to` pointing at our folder
    that llmpro did not write is left, with a note to take it out by hand. `plan_remove` lists the same.

## Tests (`tests/test_push_guard.py`)

Each test makes a temporary repo with a bare remote, sets `core.hooksPath` locally to a folder holding a
`pre-push` that runs `python -m llmpro hook push-guard "$@"` (no real gitpro hooks), and uses a store
from the fixtures with a raw session file holding a fake token.

- A push whose commit adds a line with a known format (a fake `ghp_` token of the right length) is
  stopped, exit status of `git push` is non-zero, stderr has the short sha, path, line and the masked
  value, and never the value itself.
- A push that adds a line holding a value from the raw session file (the leak check) is stopped.
- A clean push goes through and prints nothing from llmpro.
- `LLMPRO_SKIP=1` lets the bad push through.
- A new branch: only commits not on any remote branch are checked (an old bad commit already on the
  remote is not reported).
- A deleted branch push passes.
- An allowlisted fingerprint passes.
- A broken store (for example `config.toml` unreadable) gives the "could not run" line and the push goes
  on.
- From the store itself: exit 0 without scanning.
- `scan_texts` returns hits as data and prints nothing (capsys); `llmpro check` output on the fixture
  store is the same before and after (compare with a recorded run in the test).
- Setup item: with the fake answering `guard status --json` with `chain_to` null, `setup -n` shows the
  `[guard]` line it would add and writes nothing; `setup -y` adds it, keeps a copy and the mode, and runs
  `gitpro setup -y --agent none`; a config llmpro cannot edit is left with the lines to add; with `chain_to`
  set to our folder and the script written, the item is in place; with another folder, the detail
  appears. `setup -y` writes the script with mode 0755 and the undo script removes it.

## Done when

README section 2 item 12 holds.
