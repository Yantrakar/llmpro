# 12 The harness manifest

Wave 5. Branch `spec/12-harness-manifest`. Test file `tests/test_harness_manifest.py`.

## Goal

Name the parts of the harness the store holds, in `config.toml`, and have `llmpro setup -n` show for
each part which tools get it (DECISIONS H4). This round only reports; it places nothing new.

## May edit

`src/llmpro/templates/config.toml`, `src/llmpro/store.py`, `src/llmpro/setup.py`.

## Creates

`tests/test_harness_manifest.py`.

## Uses

`store.configure(store, required=True)` reads `config.toml` into `store.CONFIG` and exits with a message
for a bad value (see how `local_identities` is checked). `setup.Item(name, missing, state, details,
todo)`, `setup.survey(store, hub, only)` (the core items, then the plugins'), `setup.POINTER_FILES`,
`setup.pointer_status(store, tool, folder, name, kind)` -> `('present'|'todo'|'absent'|'unreadable',
path)`, `setup.tilde(path)`.

## Requirements

### The section

1. `templates/config.toml` gains, after `[store]`:
   ```
   [harness]
   # The parts of the harness this store holds: what every assistant loads. Paths are inside the store.
   # llmpro setup -n shows which tools get each part. Optional parts: guardrails, subagents, scripts, hooks.
   instructions = "AGENTS.md"
   memory = "memory/"
   skills = "skills/"
   ```
2. `store.py`: `HARNESS_PARTS = ('instructions', 'memory', 'skills', 'guardrails', 'subagents',
   'scripts', 'hooks')` and `HARNESS = {}`, filled by `configure`. Without a `[harness]` section the
   first three take the values above (old stores have no section). Validation, each a clear exit like
   the others: an unknown key; a value that is not a string; an absolute path or one containing `..`.
   `harness_parts()` returns `[(part, store-relative path)]` in `HARNESS_PARTS` order.

### The report

3. `setup.survey_harness(store)` -> one `Item('harness', False, 'present', details)`: informational, it
   never makes `setup -n` exit 1 and has no todo. Added after `instruction pointers` in `survey()` (only
   when `only` is None or holds `store`).
4. Details: one line per part, `<part>: <path>  <tool> <state>, ...`, where the path ends in
   ` (not in the store)` if it does not exist. The state per tool:
   - `instructions` and `memory`: what `pointer_status` says for that tool (`present` -> placed, `todo`
     -> missing, `absent` -> not here, `unreadable` -> skip). Memory reaches a tool through AGENTS.md.
   - `skills`: claude-code is placed when `~/.claude/skills` resolves to `STORE/skills`; copilot-cli when
     `~/.copilot/skills` does, or the store is `~/.agents`; codex and opencode when the store is
     `~/.agents` (they read `~/.agents/skills` themselves). Otherwise missing.
   - Every other part: `not supported` for every tool (one word list, not per tool:
     `<part>: <path>  not placed by llmpro yet`).
   Tools are those in `POINTER_FILES`, in that order.
5. `setup -n --json` includes it like any item (`item_json`).
6. Update the module docstring's list of what setup looks at (a fifth line: "5. harness  reports which
   tools get each part named in [harness]; changes nothing").

## Tests (`tests/test_harness_manifest.py`)

- A store from the template: `harness_parts()` is the three defaults; a store whose config has no
  `[harness]`: the same.
- `[harness] subagents = "agents/"` is accepted and listed; `foo = "x"`, `skills = "/abs"`,
  `skills = "../x"`, `skills = 3`: each exits with a message naming the key.
- `setup -n` with the claude-code pointer present and the others absent: the `instructions` line says
  `claude-code placed` and `codex not here`; with `~/.claude/skills` a link to `STORE/skills`, the
  `skills` line says `claude-code placed`; the `harness` item does not change the exit code.
- A part whose path is missing in the store shows `(not in the store)`.
- `setup -n --json` has an item named `harness`.
