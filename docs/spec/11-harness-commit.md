# 11 Each chat records the harness it ran under

Wave 6 (after 09, 10, 12 are merged). Branch `spec/11-harness-commit`. Test file
`tests/test_harness_commit.py`.

## Goal

The store is the harness: instructions, memories and skills every assistant loads (DECISIONS H3, H4).
Record in each chat which commit of the store was current when the chat started, so that a lesson can
later be tied to what the assistant had loaded, and two chats can be compared by harness.

## May edit

`src/llmpro/redact.py` (`render` only), `src/llmpro/importer/history.py`, `src/llmpro/find.py` (the hit
dict only), `src/llmpro/templates/FORMAT.md` (the conversation file block only).

## Creates

`tests/test_harness_commit.py`.

## Uses

`redact.render(conv, summary='')` writes the frontmatter; `conv.started` is a datetime with tzinfo or
None. `importer/history.run(store, tools=None, only=None)` keeps the existing `summary` from the output
file's frontmatter before calling `render`, and writes only when the text differs. `tools.which(name)`
finds an outside program (`$LLMPRO_TOOL_PATH` when set, else `PATH`); `sync.git` still runs a bare
`git`, so do not use it here. `find.search` builds a hit dict per history file from its frontmatter.

## Requirements

1. `render(conv, summary='', harness='')`: when `harness` is not empty, the frontmatter has
   `harness: <value>` right after `updated`. No line when empty.
2. In `importer/history.run`, once per run: read the store's first-parent commits of `HEAD` with one
   call, `<tools.which('git')> -C STORE log --first-parent --format='%H %ct' HEAD`, into a list sorted by
   time. No commits, no git, or an error: no harness for anyone this run (do not fail the import).
3. For each chat: if its existing output file has a `harness`, keep it (like `summary`). Otherwise the
   harness is the first 12 characters of the last commit whose committer time is at or before
   `conv.started`, found with `bisect`. None when `conv.started` is None or earlier than the first commit.
4. The first run after this change adds `harness` to every chat whose source is still on this machine.
   That is a one-time rewrite of those files; say so in the report for the CHANGELOG. Chats imported on
   another machine are not touched.
5. Chats in `local/history/` get the field too (it is the store's commit, not the chat's content).
6. `find` history hits gain `harness` (empty string when absent). Memory hits do not.
7. `templates/FORMAT.md`, conversation block: add `harness: <store commit, 12 chars, when the chat
   started; kept once set>` after `updated`.
8. Speed: one git call per run, not per chat.

## Tests (`tests/test_harness_commit.py`)

- A store with three commits at known committer times (set `GIT_COMMITTER_DATE`); a chat started
  between the second and third: its file gets the second commit's 12-char id.
- A chat started before the first commit: no `harness` line.
- Re-running after a new commit: the existing `harness` is kept.
- An output file whose `harness` was set by hand to `abc`: kept.
- No git (`LLMPRO_TOOL_PATH` set to an empty folder): import succeeds, no `harness`.
- `git log` is called once for a run that imports several chats (`LLMPRO_TOOL_PATH` holds a `git` wrapper that logs each call and runs the real git).
- `find --json` history hits have `harness`.
