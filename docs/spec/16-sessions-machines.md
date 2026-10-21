# 16 Sessions on other machines

Wave 10. Branch `spec/16-sessions-machines`. Test file `tests/test_sessions_machines.py`.

## Goal

`llmpro resume` also lists the sessions on the machines in `[machines]`, asked over ssh at once (S5);
`setup -n` says whether each machine can be asked; the MCP server gets a read-only `sessions` tool (S4).

## May edit

`src/llmpro/sessions/__init__.py`, `src/llmpro/cli.py`, `src/llmpro/store.py`,
`src/llmpro/templates/config.toml`, `src/llmpro/setup.py`, `src/llmpro/mcp.py`, `tests/test_mcp.py` (only
where a test lists the tool names).

## Creates

`src/llmpro/sessions/remote.py`, `tests/test_sessions_machines.py`.

## Uses

From 15: `sessions.Row` (`as_dict`, `from_dict`), `sessions.sort(rows)`, `sessions.local_rows(line=True)`,
`sessions.clean(text)`, `sessions.FORMAT`. `store.MACHINE`, `tools.which('ssh')`, `jsonout.emit`,
`mcp.gate_find` (as the model for the new gate). Fixtures `home`, `store`, `tools_dir` (a fake `ssh`
script there).

## Requirements

1. `[machines]` in the template, commented out, with two examples: `laptop = "laptop"` and
   `work = { ssh = "me@work.example.com", llmpro = "~/.local/bin/llmpro", hostname = "work-pc" }`. A value
   is a string (the ssh target) or a table with `ssh` (required), `llmpro` (default
   `~/.local/bin/llmpro`) and `hostname` (default the name).
2. `store.machines(store)` reads `[machines]` from the store's `config.toml` itself (it does not need
   `configure`) and returns a list of `Machine(name, ssh, llmpro, error)`, without the entry whose name or
   hostname is `store.MACHINE`. An entry whose name or target does not match `[A-Za-z0-9._@-]+`, whose
   target starts with `-`, or whose value has the wrong type gets `error` set (`[machines] <name>: <why>`)
   and is never asked. A config.toml that cannot be read gives an empty list.
3. `remote.remote_string(m, args)` -> the remote command, one string joined from `shlex.quote`d words:
   the llmpro path (a path starting `~/` or `$HOME/` is sent as `"$HOME"/` followed by `shlex.quote` of the rest),
   then each of `args`. Nothing the user typed reaches the remote shell unquoted. `remote.command(m, args,
   control=None)` -> argv: `ssh -T -o BatchMode=yes -o ConnectTimeout=5 [-o ControlPath=<control>] -- <ssh>
   <remote_string>`.
4. `remote.ask(m, timeout=10, control=None)` runs
   `remote.command(m, ['resume', '--local', '--json', '--no-line'])` (with `-o ControlPath=<control>` when
   given) and returns `(rows, error)`. Each row is read with `Row.from_dict`; a bad row is dropped and
   counted in the error. The asking side sets `machine` to `m.name` and `line` to `''` on every row.
   Errors, each one line:
   - ssh exits 255: `<name>: ssh could not log in without a password; set up a key, or run ssh <ssh> once`
     when its stderr says `Permission denied`, else `<name>: cannot reach <ssh>`.
   - exit 127 or `not found` on stderr: `<name>: llmpro not found at <llmpro>; set llmpro in [machines]`.
   - not JSON, or `format` missing or different:
     `<name>: its llmpro does not know resume --json; run pipx upgrade llmpro there`.
   - exit 2 with `live sessions need Linux` on stderr: `<name>: live sessions need Linux`.
   - timeout: `<name>: no answer within <timeout> seconds`.
5. `sessions.all_rows(machine=None, control=None, line=True, timeout=10)` -> `(rows, errors)`: this machine's rows and
   every machine's, asked in parallel (threads), merged and sorted; `errors` is a list of
   `{"machine", "error"}`, including machines with `error` from 2. `machine` limits it to one name (this
   machine's own name is allowed). `control` maps a machine name to a ControlPath (used by 18).
6. `llmpro resume` without `--local` uses `all_rows`. On a terminal it prints `asking N machines...` on
   stderr, then the list once, after every machine has answered or timed out. JSON `errors` carries the
   errors; exit stays 0.
7. `setup -n` gains a `machines` item with one line per machine: `ok (N open)` or the error from 4. It is
   a report only: never missing, and apply does nothing for it.
8. The MCP tool `sessions` (no arguments) returns the rows as `--json` gives them, through a gate
   `gate_sessions` that removes every `line` and every `local_only` row itself and returns an error when
   the output does not parse. Its description says it is read-only and that `llmpro resume` continues a
   session.

## Tests

A fake `ssh` in `tools_dir` that records its argv and answers per target from a JSON file: good rows (one
with a `line`, one with another `machine`), exit 255 with `Permission denied`, exit 127, a non-JSON banner,
an old format, a sleep past the timeout. The argv: `--` before the target, the remote command quoted, a
`~/` path sent as `"$HOME"/...`. Parallel, with `timeout=1`: two slow machines finish in about one second, not two. This
machine's entry skipped by name and by hostname. Bad entries (a target `-oProxyCommand=x`, a name with a
space, a number as value) become errors and are not asked, and `llmpro sync -n` still works. The MCP gate
drops lines and local-only rows even when the rows carry them. `setup -n` shows the machines lines and its
exit code does not change because of them.
