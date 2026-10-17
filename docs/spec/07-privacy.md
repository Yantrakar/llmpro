# 07 Privacy by git identity

Wave 3. Branch `spec/07-privacy`. Test file `tests/test_privacy.py`.

## Goal

Today a project's chats stay out of the shared store only when its folder is listed in `[privacy]
local_only`. People who keep work and personal code apart already told gitpro which identity each repo
uses. With `[privacy] local_identities = ["work"]`, every chat whose project folder is a repo gitpro
places under one of those identities stays in `local/`, without listing folders one by one. When in
doubt the chat stays local (DECISIONS I6).

## May edit

`src/llmpro/store.py`, `src/llmpro/redact.py` (only `output_path` and the call sites below),
`src/llmpro/importer/memory.py` (only its `is_local_only` calls), `src/llmpro/sync.py`,
`src/llmpro/templates/config.toml`.

## Creates

`src/llmpro/privacy.py`, `tests/test_privacy.py`.

## Uses (from 03)

```python
from llmpro import gitpro
g = gitpro.get(); g.status().state; g.has('why')
g.run_json(['why', folder, '--json'], timeout=20) -> (code, obj)   # raises gitpro.GitproError
from llmpro.setup import state_dir                                  # $XDG_STATE_HOME/llmpro
```

`gitpro why DIR --json` (00-existing): exit 0 or 1 both carry the full JSON; `expected.id` is the
identity id, or null for the default identity. Exit 2 is an error.

Tests use `fake_gitpro` with `fg.answer(['why', <folder>, '--json'], {...}, exit=0)` and `store`, `home`.

## Requirements

1. **Config.** `[privacy] local_identities` is a list of strings (identity ids from `gitpro.toml`; the
   string `"default"` means the default identity). `store.configure` reads it into
   `store.LOCAL_IDENTITIES` (same in-place pattern as `LOCAL_ONLY`; a non-list is the same error as for
   `local_only`). The template `config.toml` gains a commented example under `[privacy]` with a short
   plain explanation.
2. **The function.** `store.is_local_project(folder) -> bool`: true when `is_local_only(folder)` is, or
   when `LOCAL_IDENTITIES` is not empty and `privacy.local_by_identity(folder)` is true. Nothing else in
   llmpro calls `privacy` directly. With `LOCAL_IDENTITIES` empty, `privacy` is not imported and
   behaviour is exactly as today.
3. **Placing a folder** (`privacy.local_by_identity(folder)`):
   - Empty or None: false (a chat with no project folder; as today).
   - The folder exists and is not inside a git repo: false.
   - Its repo's top folder (`git -C folder rev-parse --show-toplevel`) is the home folder or the store:
     false. These hold global files, not a project.
   - Otherwise ask `gitpro why <top folder> --json`. `expected.id` in `LOCAL_IDENTITIES` (null counts as
     `"default"`): true; else false.
   - gitpro answers exit 2, times out, or prints something without `expected`: true, with one warning
     per folder per run: `llmpro: could not place <folder ~-shortened> with gitpro (<reason>); its chats
     stay local`.
   - The folder no longer exists: use the cached answer (item 4) if there is one; else true, with the
     same kind of warning (`folder is gone`).
   - Answers are kept per run by top folder, so each repo is asked once per sync.
4. **Cache.** `state_dir()/privacy.json`: `{"<folder>": {"id": "<id or null>", "checked": "<ISO date>"}}`,
   written after each sync run that asked gitpro (atomic replace, mode 0600). It keeps the last answer
   for folders that later disappear. It is not in the store (identity ids are personal).
5. **Gitpro missing.** When `LOCAL_IDENTITIES` is not empty and gitpro's status is not ok or it lacks
   `why`: `sync` stops before importing with `SyncFailed('[privacy] local_identities needs gitpro 0.4 or
   later (<reason>); <hint>. Or take local_identities out of config.toml.')`. `sync -n` reports the same
   as a stop reason (`ok` false in `--json`).
6. **Where it is used.**
   - `redact.output_path`: `is_local_only(conv.source_file) or is_local_project(conv.project)`.
   - `importer/memory.py`: the two calls that test a project folder (`session_cwd(slug)` and the
     `file://` workspace folder) use `is_local_project`. The slug test and the instruction importer stay
     as they are.
   - `save_manifest`'s split stays as it is: a row is local when its `output` starts with `local/`.
7. **Chats already shared.** A conversation whose manifest row already has a shared `output` and that
   now places as local is not moved and not written twice: it keeps its shared output, and the sync
   prints one line per such chat at the end, `already shared, now local by identity: <output>; move it
   by hand (see FORMAT.md)`, plus a count. Moving it is the user's choice, since it may already be on the
   hub. (Only new chats go to `local/`.)
8. **`sync -n`** lists the same chats under `"now_local": [<output>, ...]` in `--json`, and in text as
   above. It asks gitpro (read-only) but does not write the cache.
9. Performance: one `git rev-parse` per distinct project folder and one `gitpro why` per distinct repo
   per run; nothing when `local_identities` is empty.

## Tests (`tests/test_privacy.py`)

Use real `git init` folders under the temporary HOME (for example `~/code/work-app`, `~/code/hobby`) and
the fake gitpro answering `why` for each.

- `local_identities` empty: `privacy` is not in `sys.modules` after a sync of the fixture store; output
  paths are as before.
- `local_identities = ["work"]`: a chat in `~/code/work-app` (fake answers `expected.id = "work"`) goes to
  `local/history/...`; one in `~/code/hobby` (`"personal"`) to `history/...`; `"default"` with
  `expected.id` null works.
- A folder that is not a repo, the home folder as a repo, and the store: shared, and gitpro is not asked
  (check `fg.calls()`).
- gitpro answering exit 2: local, with the warning once.
- A deleted folder with a cached answer uses the cache; without one, local with the warning.
- gitpro missing or `0.3.4` with `local_identities` set: `sync --local` stops with the message and exit
  1; `sync -n --json` has `ok` false and the reason.
- A chat already shared that now places as local stays where it is, is not duplicated under `local/`,
  and is listed in the sync output and in `sync -n --json` `now_local`.
- Two chats in the same repo: gitpro is asked once.
- `privacy.json` is written with mode 0600 under the state folder, not in the store.

## Done when

README section 2 item 12 holds, and `templates/FORMAT.md` needs no change (if you find it does, say so
in the report; it is not in "May edit").
