# What exists today (2026-10-17)

## llmpro (still named agentspro in the code)

- Package `agentspro` 0.1.0 in `src/agentspro/`, built with hatchling; version in
  `src/agentspro/__init__.py` (`__version__`, and `REDACT_VERSION`). Python 3.11+, no runtime
  dependencies. Command `agentspro = agentspro.cli:main`.
- `cli.py`: `build_parser()` (argparse) and `run(argv)`; `main()` sends `hook` and `merge-driver` to
  `hot.main(argv)` before argparse is imported. Commands: `sync`, `setup`, `uninstall`, `check`, `find`,
  `mcp`, `summary`. Flags on every command: `--store PATH` (else `$AGENTS_STORE`, else `~/.agents`),
  `-q`, `-v`. `--version` prints `agentspro 0.1.0`.
- `--json` today: `check --json` prints `{"passed", "exit", "output"}`; `find --json` a list;
  `setup -n --json` `{"items": [...], "missing": [...]}`; `uninstall -n --json` `{"todo": [...]}`;
  `sync -n --json` an object. None has `ok` or `command`.
- `store.py`: `MACHINE`, `locate(arg)`, `configure(store, required)` reads `config.toml` into `CONFIG`
  and `[privacy] local_only` into `LOCAL_ONLY`; `config_paths(section, key)`, `config_list(...)`,
  `is_local_only(*paths)` (true if any path is one of the folders or under one). `data_file(store, name,
  legacy)` finds `.agentspro/<name>`.
- `redact.py` and `importer/` call `is_local_only(conv.source_file, conv.project)` and friends to send a
  local-only project's chats to `STORE/local/` (git-ignored, never pushed).
- `hot.py`: `HOOKS = ('pre-commit', 'pre-merge-commit', 'pre-push')`; `hook(name)` runs
  `check.run(toplevel(), history=(name == 'pre-push'), staged=(name != 'pre-push'))`;
  `merge_manifest(...)`; `main(argv)`. Prints `agentspro: ...` on errors.
- `sync.py`: `hook_text(name)` returns `'#!/bin/sh\nexec agentspro hook {name} "$@"\n'`;
  `MERGE_DRIVER = 'agentspro merge-driver manifest %O %A %B'`; `HOOK_NAMES`; `hooks_dir(store)`.
  `sync.run` raises `SyncFailed(message)` for a failed step.
- `check.py` (secret check, about 870 lines): part 1 `scan_patterns(text)` finds known secret formats;
  part 2 is the leak check: `raw_sources(store)` lists the tools' raw logs, values are collected from
  them, and every file is searched for those values (whole and in 8-character pieces) through keyed
  blake2b hashes cached in `.git/agentspro/` (`load_cache`, `part2_cached`, `match_hashed`). `mask(v)`
  and `fp(v)` show a value safely. Allowlist: `.agentspro/allowlist.json`. `run(store, staged, history,
  ...)` returns 0 or 1.
- `setup.py`: `Item(name, missing, state, details, todo)`, `Undo` (`run`, `raw`, `note`, `write(kind)`
  to `$XDG_STATE_HOME/agentspro/<kind>-undo-<stamp>.sh`), `survey(store, hub)` returns the four items
  (store, git settings, session folders, instruction pointers), `render(items)`, `consent(command, yes)`,
  `setup(...)`, `uninstall(...)`. `SESSION_FOLDERS`, `POINTER_FILES`.
- `mcp.py`: stdio JSON-RPC, one message per line. `PROTOCOL = '2025-06-18'`. Tools `find`, `check`,
  `sync_status`, each run as `python -m agentspro --store S <cmd> --json` in a child. serverInfo name
  `agentspro`. Not registered with any assistant.
- Tests (`tests/`): `conftest.py` fixtures `home` (temporary HOME), `store` (a temporary store),
  `onpath` (puts this Python's bin folder on PATH so hooks find the command), `machine` (fake tool
  folders, `XDG_STATE_HOME` under the temporary HOME). `smoke_wheel.py` checks an installed wheel.
- CI: `.github/workflows/ci.yml` (pytest on 3.11 and 3.13, wheel build, smoke), `release.yml` (started by
  hand; reads the version from `src/agentspro/__init__.py`), `mcp-registry.yml`.
- Local test environment: `.venv/` (made with uv) has `-e .[test]`, `build` and `pyyaml`.

## gitpro 0.4.0 (released; `main` of github.com/Yantrakar/gitpro)

Read gitpro's code with `git -C ../gitpro show origin/main:<path>` if you need more; never
edit that repo or its working tree.

- `gitpro --version --json` prints `{"ok": true, "command": "version", "version": "0.4.0", "python":
  "...", "capabilities": [...]}`. Capabilities include `setup`, `setup.dry-run` (new in 0.4), `why`,
  `why.config`, `why.commit`, `mcp`, `guard`, `agent-check`, `scan.lost`, `env`, `audit`, `report`.
- Every `--json` output is `{"ok", "command", ...}`; errors are `{"ok": false, "command", "error"}` on
  stdout. Exit 0 ok, 1 a problem found or something to do, 2 error, 3 the guard refuses.
- `gitpro setup -n --json [--agent none]`: `{"ok", "command": "setup", "dry_run": true, "config",
  "identities": [...], "default": {...}, "pending": [{"group", "what"}], "done": [...], "notes": [...]}`;
  exit 1 when `pending` is not empty. `gitpro setup -y --agent none` applies without asking. `--agent
  none` sets up no AI agent hook or MCP server.
- `gitpro why DIR --json`: `{"ok", "command": "why", "repo", "config", "remotes", "expected": {"id",
  "name", "email", "ssh_key", "sign", "file", "match": {...}}, "effective": {...}, "problems": [...],
  "settings", "commit"}`. `expected.id` is the identity id from `gitpro.toml`, or null for the default.
  Exit 0 ok, 1 problems found (the JSON is still complete), 2 error (`error` key).
- `gitpro guard status --json`: includes `installed` (`"global"` or `"none"`), `hooks_dir`, `chain_to`
  (a path or null).
- Global hooks: `core.hooksPath` points at `~/.config/gitpro/hooks/`. Its `pre-push` runs
  `gitpro guard check pre-push` (exit 3 refuses), then the repo's own `.git/hooks/pre-push`, then
  `<chain_to>/pre-push` if that file is executable, each with git's arguments (remote name, remote URL)
  and the same stdin (lines `<local ref> <local sha> <remote ref> <remote sha>`). A non-zero exit from
  either hook stops the push. `chain_to` is set by the user in `~/.config/gitpro.toml` under `[guard]`
  and baked into the hook scripts by `gitpro setup`.
- `gitpro mcp`: stdio MCP server. `PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18",
  "2025-11-25")`. Current tools: `why`, `scan`, `setup`, `env`, `audit`, `report`. Deprecated tools
  (descriptions start with "Deprecated"): `which_identity`, `check_commit`, `identities`, `lost`,
  `env_for`, `draft_report` and others. Tools that write are listed only with `--allow-execution`.
- `gitpro agent-check --agent claude`: reads a Claude Code PreToolUse hook event on stdin and answers in
  Claude Code's hook format; gitpro's own plugin runs it as
  `sh -c 'command -v gitpro >/dev/null 2>&1 || exit 0; gitpro agent-check --agent claude; exit 0'`.
- gitpro's Claude Code plugin: `.claude-plugin/plugin.json`, `.mcp.json`, `hooks/hooks.json`,
  `skills/gitpro/SKILL.md`.

The gitpro installed on the user's machines may be older (0.3.x has no `setup.dry-run`).
