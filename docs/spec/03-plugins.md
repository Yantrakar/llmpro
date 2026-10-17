# 03 Plugin contract, registry, git adapter

Wave 2 (after 02 is merged). Branch `spec/03-plugins`. Test file `tests/test_plugins.py`.

## Goal

llmpro finds its parts through Python entry points (DECISIONS I2) and talks to gitpro only through
gitpro's command line (I1). This package lays down the contract, the registry, the three built-in
plugins with what they need now, the gitpro adapter, the `llmpro git ...` passthrough, and the test
fixtures every later package uses. Wave 3 packages fill in setup items, MCP proxying, the push guard and
privacy on top of it.

## May edit

`src/llmpro/cli.py`, `src/llmpro/setup.py`, `pyproject.toml`, `tests/conftest.py`.

## Creates

`src/llmpro/tools.py`, `src/llmpro/gitpro.py`, `src/llmpro/plugins/__init__.py`,
`src/llmpro/plugins/base.py`, `src/llmpro/plugins/memory.py`, `src/llmpro/plugins/secrets.py`,
`src/llmpro/plugins/git.py`, `tests/fakes/fake_gitpro.py`, `tests/test_plugins.py`.

## Requirements

### Outside programs: `llmpro.tools`

```python
def which(name: str) -> str | None
```

If `$LLMPRO_TOOL_PATH` is set (a `os.pathsep` list of folders, possibly empty), look only there;
otherwise `shutil.which(name)`. Every outside program llmpro calls from now on except `git` and the
running Python (`gitpro`, `claude`, `codex`) goes through it.

### The gitpro adapter: `llmpro.gitpro`

```python
MIN_VERSION = (0, 4, 0)
class Gitpro:                       # one per process; cheap to build
    path: str | None                # the program, or None
    version: str | None             # from --version --json
    capabilities: frozenset[str]
    def status(self) -> "Status"    # ok / missing / too_old, see below
    def has(self, capability: str) -> bool        # status ok and capability listed
    def run_json(self, args: list[str], timeout: float = 30) -> tuple[int, dict]
        # runs [path, *args]; stdin is /dev/null; returns (exit code, parsed stdout object)
        # raises GitproError(message) if the program is missing, times out, or stdout is not one JSON object
    def run(self, args: list[str]) -> int          # stdin/stdout/stderr inherited; returns the exit code
def get() -> Gitpro                 # cached for the process; reset_cache() for tests
class GitproError(Exception)
```

- Path: `$LLMPRO_GITPRO` if set (an empty or missing file there means "missing"; never fall back to PATH),
  else `tools.which('gitpro')`.
- Version: run `gitpro --version --json` once (timeout 10 s); a failure, a non-JSON answer, or a version
  below `MIN_VERSION` gives `too_old` with the reason. Versions compare as integer tuples of the first
  three dot-separated numbers; anything after (`0.4.1rc1`) is ignored for the comparison.
- The child environment drops nothing, but sets `NO_COLOR=1` and `GITPRO_NO_DEPRECATION=1`.

### The contract: `llmpro.plugins.base`

```python
API = 'llmpro-plugin.v1'

@dataclass
class Status:
    state: str            # 'ok' | 'missing' | 'too_old' | 'error'
    reason: str = ''      # one line, for people
    hint: str = ''        # what to do, e.g. 'pipx install gitpro'

@dataclass
class Context:
    store: str            # the store folder (may not exist yet)
    home: str

class SetupItem:
    """One thing setup looks after, owned by a plugin."""
    name: str                                 # shown in the preview, e.g. 'gitpro', 'mcp: claude code'
    def survey(self, ctx) -> 'setup.Item'     # setup.Item(name, missing, state, details, todo)
    def apply(self, ctx, undo: 'setup.Undo') -> None   # only called when survey().missing; raise setup.SetupError to stop
    def remove(self, ctx, undo) -> list[str]  # for uninstall: lines done; default: nothing
    def plan_remove(self, ctx) -> list[str]   # for uninstall -n: what remove() would do; default: []

class Plugin:
    name: str = ''
    api: str = API
    def status(self, ctx) -> Status                    # default Status('ok')
    def capabilities(self, ctx) -> list[str]           # default []
    def add_commands(self, sub, common) -> dict        # default {}; returns {command name: handler(args, store) -> int}
    def setup_items(self, ctx) -> list[SetupItem]      # default []
    def mcp_child(self, ctx) -> list[str] | None       # default None; a command whose MCP tools llmpro serves
                                                       # as '<plugin name>_<tool>' (04 uses it)
```

### The registry: `llmpro.plugins`

```python
GROUP = 'llmpro.plugins'
def load() -> list[Plugin]     # sorted by name; cached per process; reset_cache() for tests
def get(name) -> Plugin | None
```

- Reads `importlib.metadata.entry_points(group=GROUP)`. Each entry point's object is a `Plugin`
  instance. One that fails to import, is not a `Plugin`, has another `api`, or repeats a name is skipped
  with one warning line on stderr (`llmpro: plugin X skipped: <reason>`); llmpro goes on.
- When llmpro runs from a source tree without installed metadata (no entry points of the group found),
  fall back to the three built-in plugins, so `python -m llmpro` from a checkout works.
- `pyproject.toml`:

  ```toml
  [project.optional-dependencies]
  git = ["gitpro>=0.4,<0.6"]
  test = ["pytest"]

  [project.entry-points."llmpro.plugins"]
  memory = "llmpro.plugins.memory:plugin"
  secrets = "llmpro.plugins.secrets:plugin"
  git = "llmpro.plugins.git:plugin"
  ```

### Built-in plugins

- `memory` (`plugins/memory.py`): capabilities `memory.sync`, `memory.find`, `memory.summary`. No
  commands of its own yet (the core commands stay in `cli.py`). `setup_items` returns `[]` (05 adds).
- `secrets` (`plugins/secrets.py`): capabilities `secrets.check`, `secrets.store-hooks`. `setup_items`
  returns `[]` (06 adds).
- `git` (`plugins/git.py`): `status()` from `gitpro.get().status()`, with hint
  `pipx install gitpro, or pipx install "llmpro[git]"` when missing and `pipx upgrade gitpro` when too old.
  Capabilities: `git` and, for each gitpro capability `c`, `git.<c>` (only when status is ok).
  Commands: `git` (see below). `mcp_child()`: `[gitpro path, 'mcp']` when `has('mcp')`, else None.
  `setup_items` returns `[]` (05 adds).

### `llmpro git ...`

`llmpro git ARGS...` runs `gitpro ARGS...` with the terminal passed through (`Gitpro.run`) and exits
with its code. All arguments after `git` go to gitpro unchanged, including `--help` and `--json`
(argparse `REMAINDER`). Missing or too old gitpro: one line on stderr with the reason and hint, exit 2.

### Wiring

- `cli.py` asks every plugin for `add_commands` when it builds the parser, and dispatches those
  commands. The hot path (`hook`, `merge-driver`) never loads the registry.
- `llmpro --version --json` gains `"plugins": [{"name", "api", "state", "reason", "hint"}]` and adds each
  plugin's capabilities to `capabilities` (still sorted, no duplicates).
- `setup.py`: `survey()` appends, after its four items, `item.survey(ctx)` for every plugin's
  `setup_items(ctx)` (plugins in name order). `setup()` applies, after its own four steps and in the same
  order, every plugin item whose survey is missing, with the same `Undo`. `uninstall` shows
  `plan_remove` lines in `-n` and calls `remove` (plugins in reverse order) before its own steps. The
  JSON of `setup -n --json` gains `"plugin": "<name>"` on plugin items and `"plugins"` as in
  `--version --json`. A plugin whose `status` is not ok adds one item named `plugin <name>` with
  `missing = False`, `state` = its reason, and its hint as a detail: it informs, it does not fail
  `setup -n`.
- `setup --only NAMES` (comma-separated plugin names, plus `store` for the four core items): survey and
  apply only those.

### Test fixtures (`tests/conftest.py`, `tests/fakes/fake_gitpro.py`)

- An autouse fixture sets `LLMPRO_TOOL_PATH` to an empty temporary folder (`tools_dir`), sets
  `LLMPRO_GITPRO` to a path that does not exist, sets `LLMPRO_NO_RENAME_NOTE=1`, and resets the
  `gitpro.get()` and `plugins.load()` caches before and after each test. So no test can reach a real
  `gitpro`, `claude` or `codex`.
- `tools_dir` fixture: that folder (a `pathlib.Path`), for tests that put fake programs in it.
- `fake_gitpro` fixture: copies `tests/fakes/fake_gitpro.py` into a temporary folder as an executable
  named `gitpro` (shebang = this Python), points `LLMPRO_GITPRO` at it, and returns an object:

  ```python
  fg.version(v='0.4.0', capabilities=(...))     # answer for --version --json (default: a 0.4.0 set)
  fg.answer(args: list[str], stdout, exit=0)    # stdout: a dict/list (JSON-encoded) or a str; matched on the exact argv
  fg.mcp(tools: list[dict], results: dict[str, dict] = {}, protocols=(...))
                                                # for argv ['mcp']: a stdio MCP server with these tools
  fg.calls() -> list[list[str]]                 # every argv it was run with, in order
  fg.path                                       # the program
  ```

  The fake reads its answers from a JSON file next to it, appends each argv to a log file, and prints
  `fake gitpro: no answer for <argv>` on stderr with exit 2 for anything not set. In `mcp` mode it
  answers `initialize` (with the client's protocol version if listed, else the newest), `tools/list` and
  `tools/call` (the `results` entry for the tool name, else an error result), and ignores
  notifications.

## Tests (`tests/test_plugins.py`)

- `tools.which` honours `LLMPRO_TOOL_PATH` (finds a fake there, does not find `git` there).
- Adapter: missing (env path absent), too old (`0.3.4`), ok (`0.4.0`), a non-JSON version answer, a
  timeout (a fake that sleeps; use a short timeout argument), `run_json` with exit 1 and full JSON
  (returned, not raised), `run_json` with non-JSON (raises).
- Registry: the three built-ins load from entry points; a broken entry point (monkeypatched
  `entry_points`) is skipped with a warning; a wrong `api` is skipped; the fallback without metadata.
- `--version --json` lists the plugins with their states and the `git.*` capabilities when the fake is
  ok, and none of them when gitpro is missing.
- `llmpro git why . --json` runs the fake with `['why', '.', '--json']` and exits with its code; missing
  gitpro gives exit 2 and the hint.
- Setup wiring: a test plugin (registered through a monkeypatched `plugins.load`) with one missing item
  shows in `setup -n` and `setup -n --json` (with `"plugin"`), is applied by `setup -y` after the core
  items, its undo line lands in the same undo script, and `--only store` skips it. A plugin with status
  `missing` shows as an informing item and `setup -n` exit status does not change because of it.
- The hot path: running `cli.main(['hook', 'pre-commit'])` with a monkeypatched `hot.main` does not
  import `llmpro.plugins` (check `sys.modules`).

## Done when

README section 2 item 12 holds, and the fixtures are documented in a short docstring at the top of
`tests/conftest.py`.
