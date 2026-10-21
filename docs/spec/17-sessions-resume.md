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
   `~/.claude/projects/*/<id>.jsonl` (folder: the first `cwd` in the transcript, as under "Facts") and
   `~/.copilot/session-state/<id>/` (folder: `context.cwd` of `session.start`). Found this way, the row
   has no pid.

### Running the tool

4. `launch.command(row)` -> argv: `claude --resume <id>`, `copilot --resume <id>`, `opencode -s <id>`. Each program is found with `tools.which`; a missing one is an error
   naming it.
5. `launch.env(environ)` -> a new dict: removes the variables Claude Code, Copilot CLI and opencode
   set for their shell commands, each by name (Facts in 19, S10.10; never a whole `CLAUDE_CODE_*`,
   `COPILOT_*` or `OPENCODE_*` prefix, so a setting the user exports still reaches the tool), every
   `VSCODE_*`, `COPILOT_DEBUG_NONCE` (VS Code's Copilot extension, in its terminals) and
   `*_VSCODE_SNAP_ORIG`, and `LLMPRO_*` except `LLMPRO_SEND_HOPS`; `AGENT` when `OPENCODE` is set;
   `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, `GIT_CONFIG_VALUE_<n>`, `GIT_OPTIONAL_LOCKS` and
   `GIT_TERMINAL_PROMPT` when `COPILOT_CLI` is set (Copilot sets them for its shell commands); when
   `TERM_PROGRAM` is `vscode`, removes it and `TERM_PROGRAM_VERSION`. Everything
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
    there first` and exits 1. (20 replaces this with the takeover.) A row whose `where` is `container`
    prints `<target> runs in a container; continue it there` and exits 1, open or not, because its folder
    is a folder inside the container. An opencode row never has a pid and is always continued.
11. `llmpro resume TARGET` exits with the tool's status (README rule 9, the exception). From the prompt in
    15, the list is shown and asked again after the tool exits, so one `llmpro resume` can continue
    several sessions in turn. `resume.target` is added to `CAPABILITIES`.
12. `-n` with a target prints what it would run (machine, folder, argv) and exits 0 without running it
    or taking the lock.

## Tests

`resolve`: exact id over name over prefix, a too short prefix, two matches of one kind, short and long tool
names, `@machine`, a closed session found through `seen.json` and through the transcript folder (with its
folder). `seen`: written only by a terminal listing, not by `--json`. `env`: every nesting variable
removed and the rest kept; `TERM_PROGRAM` kept unless it is `vscode`; `AGENT` and the `GIT_*` variables
kept unless `OPENCODE` or `COPILOT_CLI` is set; an environment copied from a VS Code terminal keeps no
`VSCODE_*` and no `TERM_PROGRAM`. The lock: a second holder in another
process gives `Busy`; a bad id is refused. `run` with a fake `claude` in `tools_dir` that records its cwd
and environment and exits 3: cwd, env and status passed on; the fake reports its SIGINT setting, which
is the default. The session becomes open between the list and
the run: refused. A fake tool that writes `keybindings.json` and a `.backup`: the warning, the `diff` and
`mv -f` lines, and the file is left as the tool left it. Folder gone. Open session refused. A container row refused. Remote: the fake
`ssh` gets `-t`, `--` and the quoted target. `-n`.

## Facts (recorded by the integrator before this wave, S10.3, S10.4, S10.8)

Checked on 2026-10-21 with Claude Code 2.1.305, Copilot CLI 1.0.101 and opencode 2.0.30, with
throwaway sessions in a scratch folder.

- S10.3: `claude --resume <id>` (interactive and with `-p`) keeps the session id and the name given with
  `-n` (the record shows both). `copilot --resume <id>` keeps the id. On SIGHUP, Claude exits within
  about a second (status 129) and removes its record; Copilot exits within a second and writes
  `session.shutdown`. A half-typed message is lost in both: it was found nowhere on disk afterwards. An
  interactive start in a folder Claude has not trusted shows its trust question first.
- S10.4: Copilot keeps `askedSetupTerminals` in `~/.copilot/config.json` (on the machine checked:
  `["vscode"]`): it offers its terminal setup, which writes the Shift+Enter binding into VS Code's
  `keybindings.json`, once per terminal kind, when it runs in a VS Code terminal. `launch.env` removes
  `TERM_PROGRAM=vscode` and every `VSCODE_*`, so a tool llmpro starts does not see VS Code. The hash
  check stays, because the trigger was read from Copilot's config, not reproduced. `HOST_FILES`:
  `keybindings.json` and `settings.json` under `~/.config/Code/User/` and `~/.config/Code -
  Insiders/User/`. No probe changed either file.
- S10.8: the first line with a `cwd` in `~/.claude/projects/<slug>/<id>.jsonl` is the folder the session
  started in (it matched the slug for every transcript checked); later lines change `cwd` as the session
  moves, so only the first counts.
