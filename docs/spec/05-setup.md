# 05 One setup

Wave 3. Branch `spec/05-setup`. Test file `tests/test_setup_plugins.py`.

## Goal

`llmpro setup` covers what gitpro would set up and registers llmpro's MCP server with the assistants, in
the one preview and one question setup already has (DECISIONS I7). It also closes a gap: opencode does
not yet read the store's AGENTS.md through its config.

## May edit

`src/llmpro/plugins/git.py`, `src/llmpro/plugins/memory.py`.

## Creates

`src/llmpro/register.py`, `tests/test_setup_plugins.py`.

## Uses (from 03)

```python
from llmpro import gitpro, tools
gitpro.get().status().state            # 'ok' | 'missing' | 'too_old' | 'error'
gitpro.get().has('setup.dry-run')
gitpro.get().run_json(args, timeout=60) -> (code, obj)   # raises gitpro.GitproError
gitpro.get().run(args) -> int
tools.which('claude'), tools.which('codex')
from llmpro.plugins.base import SetupItem, Context       # SetupItem: name, survey, apply, remove, plan_remove
from llmpro.setup import Item, Undo, SetupError, state_dir, tilde
```

setup.py (from 03) calls `survey(ctx)` for each plugin item after its own four items, applies the missing
ones after its own steps with the same `Undo`, and calls `plan_remove`/`remove` in `uninstall`. Plugins
run in name order: `git`, `memory`, `secrets`.

Tests use `fake_gitpro` (`fg.version`, `fg.answer`, `fg.calls`) and `tools_dir` (put a fake `claude` or
`codex` there: a small executable Python script that logs its argv and answers by argv).

## Requirements

### gitpro's part (`plugins/git.py`)

1. `setup_items` returns `[GitproSetup()]` when gitpro's status is ok and it has `setup.dry-run`, else
   `[]` (03 already shows the plugin's state in the preview).
2. `GitproSetup.survey`: run `gitpro setup -n --json --agent none` (timeout 60 s).
   - exit 0: present, state `in place`.
   - exit 1 with `pending` entries: missing; `todo` = `"<group>: <what>"` for each pending entry; state
     `<n> to do`.
   - exit 1 where a pending entry has group `config` (no identities yet): not missing (setup cannot
     answer gitpro's first-run questions); state `no identities yet`; detail
     `run gitpro setup in a terminal once`.
   - any other result or `GitproError`: not missing; state `could not ask gitpro`; detail the reason.
3. `GitproSetup.apply`: run `gitpro setup -y --agent none` with the terminal passed through; non-zero
   exit raises `SetupError('gitpro setup failed (exit N); see its output above')`. Then
   `undo.note('gitpro setup ran; to take it back run: gitpro uninstall')`. llmpro does not try to undo
   gitpro's changes itself.
4. `plan_remove`/`remove`: one line, `gitpro's own setup is kept; gitpro uninstall removes it`. Nothing
   is run.

### MCP registration and instructions (`register.py`, returned by `plugins/memory.py`)

`plugins/memory.py` `setup_items(ctx)` returns `register.items(ctx)`: the items below, in this order,
each only when its condition holds.

5. **Claude Code** (when `tools.which('claude')`): item `mcp: claude code`.
   - Present when `claude mcp get llmpro` exits 0 (timeout 20 s; a timeout counts as not present, with
     a detail).
   - Apply: `claude mcp add --scope user llmpro -- llmpro mcp`; non-zero exit raises `SetupError`.
     Undo: `claude mcp remove --scope user llmpro`.
   - Detail, not a failure, when `claude mcp get gitpro` exits 0: `gitpro's own MCP server is
     registered too; llmpro serves its tools as git_*. To remove it: claude mcp remove gitpro`.
   - Remove (uninstall): `claude mcp remove --scope user llmpro` when present.
6. **Codex** (when `tools.which('codex')`): item `mcp: codex`, the same with `codex mcp get llmpro`,
   `codex mcp add llmpro -- llmpro mcp`, `codex mcp remove llmpro`, and the same gitpro detail with
   `codex mcp get gitpro`.
7. **opencode** (when `~/.config/opencode/` exists): item `opencode config`. Wanted in its config file:
   the absolute path of the store's `AGENTS.md` in the `instructions` list, and
   `"mcp": {"llmpro": {"type": "local", "command": ["llmpro", "mcp"], "enabled": true}}`.
   - The config file is the first of `opencode.json`, `opencode.jsonc` in that folder that exists.
   - The file can hold provider keys. Never print, log or copy its content anywhere except the backup in
     item 8. Read it only to check and edit the two keys.
   - Present when the file parses as JSON (`json.loads`) and has both wanted values.
   - No file: apply writes `opencode.json` with `"$schema": "https://opencode.ai/config.json"` and the two
     values; undo removes it.
   - A file that parses as plain JSON: apply adds what is missing (keeps every other key and the key
     order, writes with 2-space indent and a final newline), after copying the file to
     `state_dir()/backup-<stamp>/` (folder mode 0700); undo copies the backup back.
   - A file that does not parse as plain JSON (JSONC with comments or trailing commas): llmpro does not
     edit it. The item is missing; its `todo` and its apply print the JSON lines to add by hand, and
     apply leaves no undo line.
   - Remove (uninstall): only for a plain JSON file: take out the store's path from `instructions` and
     `mcp.llmpro`, with the same backup and undo; for JSONC, print what to take out.
8. Every outside command runs with stdin from /dev/null, its output captured, and a timeout; its
   stderr's last line goes into the item's details on failure. Nothing reads `~/.claude.json`,
   `~/.claude/settings.json` or Codex's `config.toml` directly: the assistants' own commands are the
   interface.

## Tests (`tests/test_setup_plugins.py`)

Use the `machine` and `store` fixtures from conftest so the four core items are in place where a test
needs that.

- gitpro set up (fake answers exit 0 for `setup -n --json --agent none`): the `gitpro` item is present;
  `setup -y` does not run `gitpro setup -y`.
- gitpro with pending items: `setup -n` lists them under `gitpro` and exits 1; `setup -y` runs
  `['setup', '-y', '--agent', 'none']` once, after the core items, and the undo script has the
  `gitpro uninstall` note.
- gitpro pending with group `config`: not missing; detail says to run gitpro setup in a terminal.
- gitpro too old (`0.3.4`) or missing: no `gitpro` item; the informing plugin item from 03 is there.
- A fake `claude` without llmpro registered: item missing; `setup -y` runs the add command with the exact
  argv; the undo script has the remove command. With `claude mcp get gitpro` exiting 0, the detail
  about gitpro's server appears. No `claude` on the tool path: no item.
- The same for a fake `codex`.
- opencode: no file (created; undo removes); plain JSON with other keys (kept, order kept, backup made,
  undo restores); already set (present, not rewritten: mtime unchanged); JSONC with a comment (not
  edited, byte for byte the same; the lines to add are printed). A fake provider key in the file never
  appears in any output captured by the test.
- `uninstall -n` lists the removals for registered Claude Code and plain JSON opencode, and `uninstall -y`
  runs them.

## Done when

README section 2 item 12 holds.
