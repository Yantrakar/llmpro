# 14 Claude Code hooks and guardrails through a store plugin

Wave 8 (after 13 is merged). Branch `spec/14-harness-claude-plugin`. Test file
`tests/test_harness_plugin.py`.

## Goal

Place the store's Claude Code hooks and guardrails without writing Claude Code's settings files
(DECISIONS P3, P5): the store holds a local marketplace with two plugins, and setup adds and installs
them with Claude Code's own `claude plugin` commands.

## May edit

`src/llmpro/harness.py`, `src/llmpro/setup.py`, `src/llmpro/templates/FORMAT.md`.

## Creates

`tests/test_harness_plugin.py`.

## Uses

From 13: `harness.PLACES`, `harness.state(store, part, tool)`, `harness.apply(store, undo)`, the part
READMEs, `plan_uninstall`'s harness links. `tools.which('claude')`. `setup.Undo.run`. Fixtures `home`,
`store`, `tools_dir` (put a fake `claude` script there).

## Requirements

### The files in the store

1. setup creates, when missing (each counts in the harness todo like a missing folder):
   - `STORE/.claude-plugin/marketplace.json`:
     `{"name": "llmpro-harness", "owner": {"name": "llmpro"}, "plugins": [{"name": "harness-hooks",
     "source": "./hooks/claude-code"}, {"name": "harness-guardrails", "source": "./guardrails/claude-code"}]}`
     (2-space indent, trailing newline).
   - For each plugin folder: `.claude-plugin/plugin.json` `{"name": "<plugin>", "description": "<one line>"}`
     (no `version`, so nothing is pinned) and `hooks/hooks.json` `{"hooks": {}}`.
   An existing file is never rewritten. A `marketplace.json` that does not parse, or lacks one of the two
   plugins, is reported (detail: the file and what is wrong) and the plugin cells are not missing.

### State

2. The claude-code cells of `hooks` and `guardrails` stop being `unsupported`. Their state:
   - `not here`: `~/.claude` is missing, or `tools.which('claude')` finds nothing.
   - `placed`: `claude plugin marketplace list --json` has an entry named `llmpro-harness` whose path is
     the store (real paths compared), and `claude plugin list --json` has `<plugin>@llmpro-harness` with
     `enabled` true.
   - `missing`: otherwise, with todo lines `claude plugin marketplace add <store>` (once) and
     `claude plugin install <plugin>@llmpro-harness --scope user`.
   - `linked elsewhere`: a marketplace named `llmpro-harness` points at another path; detail names it and
     `claude plugin marketplace remove llmpro-harness` to let setup place it.
   - `disabled`: installed but not enabled; detail `claude plugin enable <plugin>@llmpro-harness`; not
     missing (the user turned it off).
   Call each `claude` command at most once per survey (cache within the call), with a 30 s timeout. A
   failure or unparsable output makes the cells `unknown` with a detail giving the command, its exit status
   and the first line of its stderr; `unknown` is not missing.
3. The guardrails line for codex, copilot-cli and opencode keeps 13's reason.

### Apply and undo

4. `harness.apply` runs, after 13's links: `claude plugin marketplace add <store>` if needed, then
   `claude plugin install <plugin>@llmpro-harness --scope user` for each missing plugin. A command that fails
   stops apply with a `SetupError` naming the command, its exit status and stderr, and saying what was
   already done (the undo script covers it). Undo lines: `claude plugin uninstall <plugin>@llmpro-harness
   --scope user` and `claude plugin marketplace remove llmpro-harness` (the latter only if setup added it).
   Use the path `tools.which` found in the commands it runs; the undo lines use plain `claude`.

### Uninstall

5. `plan_uninstall` also lists the two plugins when installed from `llmpro-harness`, and the marketplace.
   `uninstall` runs `claude plugin uninstall ...` for each and `claude plugin marketplace remove
   llmpro-harness`; undo lines add and install them again. The store keeps its files. The todo says the
   hooks stay in `STORE/hooks/claude-code/hooks/hooks.json`.

### Docs

6. `templates/FORMAT.md`: one line in the layout for `.claude-plugin/marketplace.json`, and the wiring
   paragraph from 13 says Claude Code gets hooks and guardrails through the `llmpro-harness` marketplace.

## Tests (`tests/test_harness_plugin.py`)

A fake `claude` in `tools_dir`: a Python script that keeps its state in a JSON file under `tmp_path`,
answers `plugin marketplace list --json`, `plugin list --json`, `plugin marketplace add PATH`,
`plugin install ID --scope user`, `plugin uninstall ID --scope user`, `plugin marketplace remove NAME`, and
logs every argv.

- No `claude` found: both cells `not here`, no `claude` call, nothing missing from them.
- With the fake and `~/.claude`: `setup -n` shows `claude-code missing` on the hooks and guardrails lines
  and the todo has the add and two install lines; `setup -y` writes the marketplace and plugin files with
  the exact JSON above and calls add once and install twice; `setup -n` then exits 0 and makes no add or
  install call.
- Existing `marketplace.json` with other content: left unchanged and reported.
- A marketplace `llmpro-harness` at another path: `linked elsewhere`, no call that changes anything.
- A plugin installed but disabled: `disabled`, not missing.
- The fake fails `plugin install`: setup exits 2 with the command, status and stderr in the message, and
  the undo script removes the marketplace it added.
- The fake prints garbage for `plugin list --json`: `unknown`, with the command in the detail.
- Uninstall: calls uninstall for both plugins and marketplace remove; the files stay in the store; the undo
  script holds the add and install lines.

## Done when

README section 2 item 12 holds. Manual check for the integrator (agents never run the real `claude`):
`claude plugin marketplace add <store>` then `claude plugin list --json` shows both plugins.
