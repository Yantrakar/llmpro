# 02 Rename to llmpro, version JSON, one JSON envelope

Wave 1. Branch `spec/02-rename`. Test file `tests/test_rename.py`.

## Goal

The tool is called llmpro everywhere a user sees it: the distribution, the import package, the command,
the messages, the MCP server name. `agentspro` keeps working as an alias (DECISIONS I3). Every `--json`
output gains the envelope `{"ok", "command", ...}`. `llmpro --version --json` lists capabilities.

## May edit

- Move `src/agentspro/` to `src/llmpro/` with `git mv` (every file in it, including `templates/`,
  `importer/`, `sources/`), then edit any file under `src/llmpro/`.
- `pyproject.toml`, `README.md` (names and commands only; 08 rewrites it later), `docs/releasing.md`
  (paths and names only), `.github/workflows/ci.yml`, `.github/workflows/release.yml`,
  `.github/workflows/mcp-registry.yml` (paths only), `tests/conftest.py`, `tests/smoke_wheel.py`, and
  every `tests/test_*.py` (imports and expected names only).

## Creates

- `src/llmpro/jsonout.py`, `tests/test_rename.py`.

## Must not edit

`names/`, `docs/spec/`, `CHANGELOG.md`.

## Requirements

1. **Package.** `pyproject.toml`: `name = "llmpro"`; version from `src/llmpro/__init__.py`; wheel
   `packages = ["src/llmpro"]`. Description unchanged in meaning. Scripts:
   `llmpro = "llmpro.cli:main"` and `agentspro = "llmpro.cli:alias_main"`.
2. **Imports.** Every `from agentspro ...`/`import agentspro` in `src/` and `tests/` becomes `llmpro`.
   `python -m llmpro` works (`__main__.py`). There is no `agentspro` import package any more.
3. **Names in text.** Every user-facing `agentspro` in messages, help, docstrings that are printed, the
   templates (`templates/AGENTS.md`, `templates/FORMAT.md`, `templates/config.toml`) and the undo script
   header becomes `llmpro`, except where requirement 6 keeps the old name. `prog='llmpro'` in argparse.
4. **Alias.** `alias_main(argv=None)`: if `argv[0]` (after `sys.argv[1:]`) is `hook` or `merge-driver`,
   behave exactly like `main` and print nothing extra. Otherwise print once to stderr
   `agentspro is now llmpro; this name keeps working for now` and then behave like `main`. If the
   environment variable `LLMPRO_NO_RENAME_NOTE=1` is set, print nothing.
5. **Hooks and merge driver.** New hook scripts written by `sync`/`setup` say
   `exec llmpro hook <name> "$@"`; `MERGE_DRIVER = 'llmpro merge-driver manifest %O %A %B'`. A store whose
   hooks or merge driver still say `agentspro` counts as set up correctly by `setup -n` (both texts are
   accepted), and `setup` rewrites them to `llmpro` only when it rewrites them for another reason; it
   never reports them as missing. `uninstall` removes hooks with either text and unsets the driver with
   either value. Add both texts as module constants (`OLD_HOOK_TEXT` style) so later packages can use
   them.
6. **Kept names.** The store folder `.agentspro/` (allowlist), the cache folder `.git/agentspro/`,
   `$AGENTS_STORE`, `tools/secretcheck_allowlist.json` (legacy) keep their names. `CACHE_VERSION` keeps
   its formula (the cache is dropped once because the version string changes; that is fine).
7. **State folder.** `setup.state_dir()` returns `$XDG_STATE_HOME/llmpro` (default
   `~/.local/state/llmpro`). Add `setup.OLD_STATE_DIR_NAME = 'agentspro'` and a function
   `state_dirs()` returning `[new, old]` for code that looks for earlier undo scripts or backups.
8. **MCP name.** `mcp.py`: serverInfo name `llmpro`; children run `python -m llmpro`. Tool descriptions say
   `llmpro check --fix` and `llmpro sync`.
9. **Envelope.** `src/llmpro/jsonout.py` with:

   ```python
   def envelope(command: str, obj, ok: bool) -> dict
       # {"ok": ok, "command": command, **obj} for a dict obj; for a list: {"ok", "command", "results": obj}
   def emit(command: str, obj, ok: bool = True) -> None   # print json.dumps(envelope(...)) and a newline
   ```

   Use it for every `--json` output: `check --json` (keeps `passed`, `exit`, `output`; `ok = passed`),
   `check --near-miss --json`, `find --json` (`results` = the list it printed before; `ok` true even when
   nothing is found), `setup -n --json` and `uninstall -n --json` (keep `items`, `missing`, `todo`;
   `ok` = nothing missing / nothing to do), `sync -n --json` (keep its keys; `ok` = sync would not stop).
   Update `mcp.py` if it reads any of these outputs; the MCP tool results stay the printed JSON text.
10. **Version.** `llmpro --version` prints `llmpro 0.1.0`. `llmpro --version --json` prints
    `{"ok": true, "command": "version", "version", "python", "capabilities": [...]}` and exits 0, without
    needing a store. Capabilities, sorted: `check`, `check.json`, `find`, `find.json`, `mcp`, `setup`,
    `setup.dry-run`, `summary`, `sync`, `sync.dry-run`, `uninstall`, `version.json`. Keep them in one
    tuple `CAPABILITIES` in `cli.py` so later packages add to it. (03 adds plugins to this output.)
11. **CI and release.** `release.yml` reads the version from `src/llmpro/__init__.py`; its "Ready for
    PyPI" check now passes for the name (the name is llmpro). `smoke_wheel.py` checks the `llmpro` import,
    `python -m llmpro --version`, serverInfo name `llmpro`, and that both console scripts are in the
    wheel's `entry_points.txt`.
12. **README and releasing.md**: replace the command and package names; `pipx install` lines name
    `llmpro`. No other rewrite.

## Tests (`tests/test_rename.py`)

- `import llmpro` works; `importlib.util.find_spec('agentspro')` is None in the source tree.
- `cli.main(['--version'])` prints `llmpro <version>`; `--version --json` prints the envelope with the
  capabilities above, sorted, and exits 0 with no store present.
- `alias_main(['find', ...])` prints the rename note on stderr once; with `LLMPRO_NO_RENAME_NOTE=1` it
  prints none; `alias_main(['hook', ...])` and `alias_main(['merge-driver', ...])` print no note (use a
  monkeypatched `hot.main` that returns 0).
- `check --json`, `find --json`, `setup -n --json`, `uninstall -n --json`, `sync -n --json` each print an
  object with `ok` and the right `command`, and still carry their old keys.
- A store whose hooks say `exec agentspro hook <name> "$@"` and whose merge driver says `agentspro
  merge-driver ...`: `setup -n` does not list the git settings as missing; `uninstall -y` removes those
  hooks and unsets the driver.
- New hooks say `exec llmpro hook`. `setup.state_dir()` ends in `/llmpro`.
- The MCP `initialize` reply has serverInfo name `llmpro`.
- No file under `src/llmpro/` prints the word `agentspro` except the alias note, the kept names in
  requirement 6, and the old hook and driver texts (a grep test over the source, with that exact list
  of allowed lines).

## Done when

The rules in README section 2 item 12 hold, `llmpro` and `agentspro` both run from the wheel in
`smoke_wheel.py`, and `git grep -n agentspro -- src tests` shows only the allowed lines.
