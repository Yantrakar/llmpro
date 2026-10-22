# 23 Time limits for the MCP check and the agent hook; the sync's no-op cache

Round 6 (2026-10-23). Four findings a gitpro session reported after reading 22, folded into 0.1.2 before
its release. A follow-up to 04 (MCP), 14 (the Claude Code plugin's hook) and the sync's no-op cache.

## Facts

- `mcp.call` runs `llmpro check --json` with `subprocess.run` and no timeout. The server answers one request
  at a time, so while the check runs the assistant gets no answer to anything. The check reads every raw
  source the cache does not hold, as the push guard did before 22 (16 minutes on 2026-10-22).
- Part 1 of the check (formats) over the real store took 3.7 s on 2026-10-23 (`check --part 1`, 12 cores).
- `mcp_proxy` already limits its gitpro child: `LLMPRO_MCP_CHILD_TIMEOUT`, 30 s by default.
- `hooks/hooks.json` gives the PreToolUse hook `"timeout": 10`. `agentcheck.TIMEOUT = 8` is applied to each
  of its two gitpro calls (`--version --json` when the cache misses, then `agent-check`), so the hook could
  take 16 s, past the 10 s after which Claude Code stops it.
- `importer/all.py` keys the sync's no-op cache (`.git/agentspro/noop-redact.json`: the digests of store
  files that `redact()` left unchanged) on `f'{__version__}+{REDACT_VERSION}'`, so every release drops it
  and the next sync redacts every store file again.
- What `redact()` returns depends only on the code in `redact.py` from `# ---- redaction` to
  `# ---- text helpers`. `apply_config()` and `[redact] never_sweep` change only `_NO_SWEEP`, which only
  `learn()` reads, which only fills `LEARNED` for `sweep()`; the sweep always runs and is not cached.
  Nothing in that section reads the config, the environment, the login or the machine name.
- `REDACT_VERSION` is recorded in the store's `config.toml`, and an llmpro older than the store refuses to
  sync it. Raising it is a decision about every machine, not about one cache.
- `noop-redact.json` is written with `write_text`, so it gets the umask's mode (0664 here). The leak cache
  files next to it are 0600.

## Changes

### 1. `llmpro check --budget SECONDS`; the MCP check uses it (`check.py`, `cli.py`, `mcp.py`)

1. `llmpro check --budget SECONDS`: the leak check keeps to the rules of 22 change 2, with the deadline
   counted from the start of the check. Part 1 runs in full. Without `--budget`, nothing changes.
2. When raw sources were not read in full, the output has one more line, after `leak check: N hits`:
   `leak check: N raw logs not read in full in the time; checked against what the cache holds. Run llmpro check to bring the cache up to date.`
   With `--json`, the result also carries `late`: that number (0 without `--budget`, or when all were read).
   The exit status is as before.
3. The MCP `check` tool runs `llmpro check --json --budget 20`, and its description names `late`.
   `--budget` with `--no-cache` is a usage error (exit 2): without the cache there is nothing to check against.
4. A backstop for that call: `mcp.CHECK_TIMEOUT = 60` seconds. The child runs in its own process group; when
   the time is up the whole group (the check and its worker pool) is stopped with SIGKILL, and the tool
   answers with an error: `llmpro check did not finish in 60 s; run llmpro check in a terminal.` The other
   tools (`find`, `sync_status`, `sessions`) are unchanged.

### 2. One time budget for the agent hook (`agentcheck.py`)

`agentcheck.TIMEOUT` becomes `BUDGET = 8` seconds for the whole hook, counted from the start of `run()`. The
version call gets what is left; the `agent-check` call gets what is left after it. With nothing left, the hook
does not start the call and exits 0 with no output, as it does today when gitpro is slow. This leaves 2 s of
the 10 s for starting Python. `hooks.json` is unchanged.

### 3. The sync's no-op cache survives a release (`importer/all.py`)

`CODE = f'redact{REDACT_VERSION}+{h}'`, where `h` is the first 16 hex digits of the sha256 of `redact.py`'s
text from `# ---- redaction` up to `# ---- text helpers`, read when the module is loaded. When the source
cannot be read or the markers are missing, `h` is `__version__`, as today. The cache is then dropped when the
redaction code changes, and not otherwise. A hash is used rather than a pin test with a version to raise by
hand (as in 22) because the version here, `REDACT_VERSION`, means more than this cache (Facts).

The first sync after upgrading to 0.1.2 still redacts every file once, since the key changes form.

### 4. The no-op cache is private (`redact.py`, `importer/all.py`)

`write_text(path, text, binary=False, mode=None)`: with a mode, the temporary file is created with that mode,
so the file has it from the start, and an existing file gets it when it is replaced. `save_noop` passes
`0o600`.

## Tests

- `tests/test_mcp_gate.py`: `command('check', {})` is `['check', '--json', '--budget', '20']`, and the budget
  is under `CHECK_TIMEOUT`. With `CHECK_TIMEOUT` made tiny, `call` answers with the error. `run_limited` on a
  child that starts a sleeping worker returns None in time, and the worker is gone too. A check in time
  answers as before, with `late` 0.
- `tests/test_push_guard.py` (it has the raw-log helpers): `check --json --budget 0` on a store with an
  uncached raw log reports `late` 1 and the line, and caches nothing for it; a run without `--budget` then
  reports `late` 0 and caches it. `--budget` with `--no-cache` exits 2.
- `tests/test_bundle.py`: with gitpro slow on both calls (6 s each, each within the old 8 s), the hook exits
  0 with no output in under 10 s. The existing slow-gitpro test still holds.
- `tests/test_sync.py`: `CODE` names `REDACT_VERSION` and not `__version__`; `code_key()` is unchanged by an
  edit outside the redaction section, changes with one inside it, and falls back to `__version__` without
  the markers or the file. `save_noop` leaves the file 0600, also when it was 0664 before.

## Release

Part of 0.1.2 (22): the `CHANGELOG.md` section gains these items. The user runs the release.
