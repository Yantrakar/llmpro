# 13 Setup places the harness

Wave 7. Branch `spec/13-harness-place`. Test file `tests/test_harness_place.py`.

## Goal

`[harness]` names all seven parts, setup creates the missing part folders, and setup links each part into
every tool that loads it from a folder or file (DECISIONS P1, P2, P4, P5). The Claude Code plugin for hooks
and guardrails is package 14; until then those two cells say `not placed yet`.

## May edit

`src/llmpro/templates/config.toml`, `src/llmpro/templates/FORMAT.md`, `src/llmpro/store.py`,
`src/llmpro/setup.py`, `tests/test_harness_manifest.py` (only to follow the new defaults and the item that
can now be missing), `tests/test_setup.py` (only where a test assumed the harness item is never missing).

## Creates

`src/llmpro/harness.py`, `tests/test_harness_place.py`.

## Uses

`store.HARNESS_PARTS`, `store.HARNESS_DEFAULTS`, `store.load_harness(store)`, `store.harness_parts()`;
`setup.Item(name, missing, state, details, todo)`, `setup.Undo` (`run`, `raw`, `note`),
`setup.under_home(rel)`, `setup.tilde(path)`, `setup.POINTER_FILES`, `setup.harness_tool_states`,
`setup.survey_harness(store)`, `setup.plan_uninstall`, `setup.uninstall`. Fixtures `home`, `store`.

## Requirements

### Defaults (P1)

1. `HARNESS_DEFAULTS` holds all seven: `instructions = "AGENTS.md"`, `memory = "memory/"`,
   `skills = "skills/"`, `subagents = "agents/"`, `hooks = "hooks/"`, `guardrails = "guardrails/"`,
   `scripts = "scripts/"`. The template's `[harness]` lists the same seven, uncommented, and its comment
   says what each is in a few words. A store whose `[harness]` names only some parts gets the defaults for
   the rest (as today).
2. A part whose folder is missing in the store is created by apply, with `README.md` at its top. The README
   is short plain English: what goes in the part, the per-tool subfolders (`agents/<tool>/`, `hooks/<tool>/`,
   `guardrails/claude-code/`), and where each tool gets it (the P2 row). `instructions` (a file) and
   `memory/` are never created here; the store already has them.

### The table (`harness.py`, P2)

3. One table, `PLACES`, of `(part, tool, place, target)`:
   - `place` is the path under HOME the tool reads, or `None`.
   - `target` is the path inside the part folder that it links to (`''` for the part folder itself), or a
     string starting `'unsupported:'` followed by the reason shown to the user.
   Rows, tools in `POINTER_FILES` order:
   - skills: claude-code `.claude/skills` -> `''`; copilot-cli `.copilot/skills` -> `''`; codex `None` ->
     `unsupported: Codex reads only ~/.agents/skills`; opencode `.config/opencode/skills` -> `''`. When the
     store is `~/.agents`, copilot-cli, codex and opencode are `placed` without a link (they read it
     themselves; keep `READS_AGENTS_SKILLS`).
   - subagents: claude-code `.claude/agents` -> `claude-code`; codex `.codex/agents` -> `codex`;
     copilot-cli `.copilot/agents` -> `copilot-cli`; opencode `.config/opencode/agents` -> `opencode`.
   - hooks: claude-code `None` -> `unsupported: not placed yet`; codex `.codex/hooks.json` ->
     `codex/hooks.json` (a file); copilot-cli `.copilot/hooks` -> `copilot-cli`; opencode
     `.config/opencode/plugins` -> `opencode`.
   - guardrails: claude-code `None` -> `unsupported: not placed yet`; the other three `None` ->
     `unsupported: <Tool> loads hooks from one place; put guardrails in hooks/<tool>/`.
   A comment above the table names the documentation each row comes from (the sources in DECISIONS P2).
4. `harness.state(store, part, tool)` -> `(state, detail)`, where state is one of:
   - `placed`: the place is a symlink whose real path is the target's real path (or the skills rule
     above).
   - `missing`: the tool's folder (the `POINTER_FILES` folder) exists and the place does not, or is a real
     folder or file with no name that the target also has. A missing target does not stop it: apply makes
     the target folder (an empty file target, `codex/hooks.json`, is never created; if neither side has it
     the state is `nothing yet` and it is not missing).
   - `not here`: the tool's folder does not exist.
   - `clash`: the place is a real folder holding a name the target also holds, or a real file while the
     target file exists. Detail: `merge <place>/<name> into <target>/<name> by hand, then run llmpro setup`.
   - `linked elsewhere`: the place is a symlink to anything else. Detail: where it points, and
     `llmpro leaves it; remove the link to let setup place it`.
   - `unsupported`: the reason from the table.
   `clash`, `linked elsewhere` and `unsupported` never make the item missing.

### Scripts (P4)

5. `harness.script_states(store)` -> one `(name, state, detail)` per regular file at the top of the
   scripts folder except `README.md`: `placed` (`~/.local/bin/<name>` is a link to it), `missing` (executable
   and the name is free), `clash` (the name is taken by something else; detail names it), `not executable`
   (detail: `chmod +x <path>, then run llmpro setup`). Only `missing` counts as missing.

### The item and apply

6. `survey_harness` keeps its line format. Each part line lists `<tool> <state>` for every tool; unsupported
   cells in one part that all share a reason may be shown once. The scripts line reads
   `scripts: scripts/  2 placed, 1 missing (name), 1 not executable (name)`, or `none yet`. Below the part
   lines, one indented line for each clash or `linked elsewhere` cell with its detail. The item is missing
   when a part folder is missing or any cell or script is `missing`; its todo then lists one line per change
   (`create scripts/ with a README.md`, `link ~/.claude/agents -> ~/.agents/agents/claude-code (moves its 2
   files into the store first)`, `link ~/.local/bin/name -> ...`).
7. `harness.apply(store, undo)`, called from `setup()` after the pointers when the item is missing, does
   the changes the todo listed, in that order, printing one stderr line each like the others (`linked
   <place> -> <target>`, `created <folder>`). Moving files in: `shutil.move` each entry into the target,
   remove the empty folder, then link. Undo lines, as `apply_sessions` writes them: remove the link, put
   back a real folder and move the moved entries back; remove a created folder only if it still holds only
   the README.
8. `--json`: the item carries its details and todo as every item does. No new keys.

### Uninstall (P5)

9. `plan_uninstall` also returns the harness links: every place in `PLACES` and every `~/.local/bin` entry
   that is a symlink resolving into the store. `uninstall` replaces each with a copy of its target
   (`shutil.copytree(..., symlinks=True)` or `copy2`), says so in its todo (`replace the link ~/.claude/agents
   with a copy of ~/.agents/agents/claude-code`), and writes undo lines that remove the copy and make the
   link again. Keep the existing order; harness links go after the pointers. Adjust the tuple
   `plan_uninstall` returns and its one caller.

### Docs

10. `templates/FORMAT.md`: the layout gains `agents/<tool>/`, `hooks/<tool>/`, `guardrails/`, `scripts/` in
    one line each, and the wiring section one paragraph on what setup links (the P2 table in prose, short).

## Tests (`tests/test_harness_place.py`)

- Defaults: a store from the template and one without `[harness]` both list all seven parts.
- With only `~/.claude` present: `setup -n` exits 1; the subagents line says `claude-code missing` and
  `codex not here`; `setup -y` creates `agents/`, `hooks/`, `guardrails/`, `scripts/` each with README.md, links
  `~/.claude/agents` to `STORE/agents/claude-code`, and a second `setup -n` exits 0.
- A real `~/.claude/agents/a.md`: after `setup -y` it is in `STORE/agents/claude-code/a.md` and the place is
  a link; running the written undo script puts `a.md` back in a real folder.
- A real `~/.claude/agents/a.md` and `STORE/agents/claude-code/a.md`: `clash`, the detail names both,
  `setup -n` exit status does not count it, `setup -y` leaves both files untouched.
- `~/.claude/agents` linked to some other folder: `linked elsewhere`, left alone.
- Codex: `~/.codex` present, `STORE/hooks/codex/hooks.json` present: the hooks line says `codex missing`;
  apply links the file. Neither side has it: `codex nothing yet`, not missing.
- The store at `~/.agents`: skills are `placed` for codex, copilot-cli and opencode without links; the
  store elsewhere: codex skills say `Codex reads only ~/.agents/skills`.
- Scripts: an executable `scripts/hello` is linked into `~/.local/bin/hello`; a non-executable one is listed
  with `chmod +x`; an existing `~/.local/bin/hello` not ours is a clash and is left alone.
- Uninstall after setup: each link becomes a real copy with the same files, the store keeps its files,
  `uninstall -n` afterwards finds nothing of the harness, and the undo script makes the links again.
- `setup -n --json`: the `harness` item has `missing` true before and false after `setup -y`.

## Done when

README section 2 item 12 holds. The report lists every file changed and any table row the agent could not
match to its documentation source.
