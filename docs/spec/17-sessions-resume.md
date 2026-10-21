# 17 Continue a session

Wave 11. Branch `spec/17-sessions-resume`. Test file `tests/test_sessions_resume.py`.

## Goal

`llmpro resume TARGET` continues a session that is not open anywhere, in this terminal, with the tool's
own resume command, in the session's folder, with a clean environment, holding the session's lock, and
checks afterwards that the tool left the host's settings as they were (S1, S6, S11). A session on another
machine is continued there through `ssh -t`. A session open elsewhere is refused with where it is open
until 20.

## May edit

`src/llmpro/sessions/__init__.py`, `src/llmpro/sessions/remote.py`, `src/llmpro/cli.py`.

## Creates

`src/llmpro/sessions/launch.py`, `src/llmpro/sessions/seen.py`, `tests/test_sessions_resume.py`.

## Uses

From 15 and 16: `sessions.Row`, `sessions.ID`, `sessions.clean`, `sessions.local_rows(line=True)`,
`sessions.all_rows(machine=None, control=None, line=True, timeout=10)`, `sessions.supported()`,
`store.machines(store)` (`Machine(name, ssh, llmpro, error)`), `remote.remote_string(m, args)`,
`remote.command(m, args, control=None)`. `tools.which(name)`. The state folder:
`$XDG_STATE_HOME/llmpro` (default `~/.local/state/llmpro`). Fixtures `home`, `store`, `tools_dir`,
`machine`.

## Requirements

### Finding the target

1. `sessions.resolve(target, rows)` -> one row, or raises `sessions.TargetError` with a message. The
   grammar is `[tool:]key[@machine]`. `tool` is a short name (`claude`, `copilot`, `opencode`) or the
   `Row.tool` name. `key` is matched in this order, and the first kind with any match wins: an exact id,
   an exact name, an id prefix of at least 6 characters. More than one match of that kind is an error
   listing each match as a full target. A number is the row number of the list just shown (only from the
   prompt).
2. `seen.py` keeps `$XDG_STATE_HOME/llmpro/seen.json` (mode 0600, at most 500 entries, oldest dropped):
   tool, id, name, folder, machine and last seen time per session, no `line`. Functions:
   `seen.record(rows)` (writes only when something changed, through a temporary file and rename),
   `seen.add(row)`, `seen.lookup(key)` -> rows for sessions not open now. Only a listing on a terminal
   without `-n` and without `--json` calls `seen.record`; the MCP tool and `--json` never write it.
3. A key that matches no open row is looked up in `seen.lookup`, then as an id prefix in
   `~/.claude/projects/*/<id>.jsonl` (folder from the transcript, as under "Facts") and
   `~/.copilot/session-state/<id>/` (folder from `session.start`). Found this way, the row has no pid.

### Running the tool

4. `launch.command(row)` -> argv: `claude --resume <id>`, `copilot --resume <id>`, and for opencode the
   form recorded under "Facts". Each program is found with `tools.which`; a missing one is an error
   naming it.
5. `launch.env(environ)` -> a new dict: removes `CLAUDECODE`, `CLAUDE_PID`, every `CLAUDE_CODE_*`,
   `COPILOT_*`, `OPENCODE_*`, `VSCODE_*` and `*_VSCODE_SNAP_ORIG`, and `LLMPRO_*` except
   `LLMPRO_SEND_HOPS`; when `TERM_PROGRAM` is `vscode`, removes it and `TERM_PROGRAM_VERSION`. Everything
   else is kept. The list is one constant, `NESTING`, with a comment saying where each entry was seen.
6. `launch.lock(row)`: a context manager holding `$XDG_STATE_HOME/llmpro/live/<tool>-<id>.lock` (folder
   0700) with a non-blocking `fcntl.flock`; held elsewhere, it raises `launch.Busy`
   (`<target> is busy with another llmpro`). It refuses an id that does not match `sessions.ID`.
7. `launch.host_snapshot()` -> `{path: sha256 or None}` for every file in `HOST_FILES` (at least
   `~/.config/Code/User/keybindings.json` and `settings.json`, and the same two under `Code - Insiders`).
   `launch.host_report(before, started)` -> lines for each file that changed: `warning: <file> changed
   while <tool> ran`, and when `<file>.backup` or `<file>.bak` is newer than `started`, `see the change:
   diff <backup> <file>` and `to put it back: mv -f <backup> <file>`. It never writes these files.
8. `launch.run(row, lock=None)`:
   1. takes `launch.lock(row)` unless `lock` is passed in (20 passes its own);
   2. reads that one session again through the adapters; if it now has a live pid, refuses with item 10;
   3. refuses when the folder no longer exists (`<folder> is gone; the session cannot be continued
      there`);
   4. prints `continuing <tool> <name or id> in <folder>`, takes `host_snapshot`, runs `launch.command` as
      a child in the session's folder with `launch.env`, in the foreground of this terminal. While it
      runs llmpro ignores SIGINT and SIGQUIT with a handler that does nothing (never `SIG_IGN`, which the
      child would inherit), and the child starts with both at their defaults;
   5. prints `host_report` on stderr, calls `seen.add(row)`, and returns the tool's exit status.
9. `launch.remote_argv(m, target)` -> `ssh -t -- <ssh> <remote_string(m, ['resume', <target without
   @machine>])>`. A remote target is run as a child; llmpro
   returns its exit status. Nothing about the session is read on this machine.
10. A target that is open (it has a pid) prints `<target> is open in <where> on <machine> (pid N); close it
    there first` and exits 1. (20 replaces this with the takeover.)
11. `llmpro resume TARGET` exits with the tool's status (README rule 9, the exception). From the prompt in
    15, the list is shown and asked again after the tool exits, so one `llmpro resume` can continue
    several sessions in turn. `resume.target` is added to `CAPABILITIES`.
12. `-n` with a target prints what it would run (machine, folder, argv) and exits 0 without running it
    or taking the lock.

## Tests

`resolve`: exact id over name over prefix, a too short prefix, two matches of one kind, short and long tool
names, `@machine`, a closed session found through `seen.json` and through the transcript folder (with its
folder). `seen`: written only by a terminal listing, not by `--json`. `env`: every nesting variable
removed and the rest kept; `TERM_PROGRAM` kept unless it is `vscode`. The lock: a second holder in another
process gives `Busy`; a bad id is refused. `run` with a fake `claude` in `tools_dir` that records its cwd
and environment and exits 3: cwd, env and status passed on; the fake reports its SIGINT setting, which
is the default. The session becomes open between the list and
the run: refused. A fake tool that writes `keybindings.json` and a `.backup`: the warning, the `diff` and
`mv -f` lines, and the file is left as the tool left it. Folder gone. Open session refused. Remote: the fake
`ssh` gets `-t`, `--` and the quoted target. `-n`.

## Facts (recorded by the integrator before this wave, S10.3, S10.4, S10.8)

To be filled in. If the trigger of the Copilot `keybindings.json` write is found and avoidable through
the environment, add it to `launch.env` and test it.
