# 19 Send a message to a session

Wave 13. Branch `spec/19-sessions-send`. Test file `tests/test_sessions_send.py`.

## Goal

`llmpro send TARGET TEXT` gives one message to a session that is not open, to an opencode session, or to
a new session, and prints its answer, so the user or one assistant can ask another (S8, S11). This
package also documents `resume` and `send` for users and assistants.

## May edit

`src/llmpro/sessions/__init__.py`, `src/llmpro/sessions/launch.py`, `src/llmpro/sessions/seen.py`,
`src/llmpro/cli.py`, `src/llmpro/store.py`, `src/llmpro/templates/config.toml`, `README.md`,
`skills/llmpro/SKILL.md`.

## Creates

`src/llmpro/sessions/send.py`, `tests/test_sessions_send.py`.

## Uses

From 15 to 18: `sessions.resolve`, `sessions.all_rows`, `sessions.clean`, `launch.env`, `launch.lock(row)`
(`launch.Busy`), `launch.host_snapshot()`, `launch.host_report(before, started)`, `launch.NESTING`, `seen.add(row)`,
`store.machines(store)`, `remote.command(m, args, control=None)`, `store.sessions_config(store)`,
`store.is_local_project(folder)`. `tools.which`. `jsonout.emit`.

## Requirements

1. `llmpro send TARGET [TEXT] [--model MODEL] [--folder DIR] [-n] [--json]`. TEXT `-` or missing reads
   stdin (at most 64 KB). TARGET is a target as in 17, or `<tool>:new[@machine]` for a new session, which
   runs in `--folder` (default the current folder; a remote new session needs `--folder`). When
   `sessions.supported()` is false, exit 2 as in 15.
2. Channels, by tool and row (`send.channel(row)` -> argv and how the text is given):
   - not open, Claude Code: `claude -p --resume <id>`;
   - not open, Copilot CLI: `copilot -p --resume <id>`;
   - opencode, open or not: the form recorded under "Facts";
   - new: the same without `--resume`, plus `--model` when given;
   - open in a terminal (Claude Code or Copilot CLI): refused, exit 1, `<target> is open in <where> on
     <machine>; llmpro does not type into an open session`.
   The text goes on stdin when the tool reads its prompt from stdin, else as the last argument after `--`
   (as under "Facts"). Text starting with `-` is refused when neither is possible. No flag that allows
   tools, skips permissions or widens access is ever added (S8); `--model` is the only one passed through.
3. Limits, checked in this order before anything runs:
   - hops are `max(--hops, LLMPRO_SEND_HOPS)`; at or above `[sessions] max_hops` (default 2): exit 1,
     `this message would be hop N; the limit is max_hops in [sessions]`. This guards against loops made by
     mistake and is not a security boundary.
   - the caller is inside a session when `LLMPRO_SEND_HOPS` is set, the hidden `--inside` was given, or
     any variable in `launch.INSIDE` is present. `INSIDE` is a new constant in `launch.py`: only variables
     the tools set for the shell commands they run (as under "Facts"), never `VSCODE_*`, so the user in an
     editor terminal is not inside. From inside, a local-only target is refused: a row with `local_only`
     (also a remote row), or for `tool:new` a `--folder` where `is_local_project` is true. Exit 1,
     `<target> is in a local-only project; send to it from your own terminal`. This check stops
     mistakes; it is not a security boundary.
   - `launch.lock(row)` is taken and held for the whole send (`Busy`: exit 1); after it, the session is
     read again and refused if it is now open. A new session has no id yet and takes no lock.
   - with the lock held, more than `[sessions] sends_per_hour` (default 20) sends to that session in the
     last hour, counted in `$XDG_STATE_HOME/llmpro/sends.json` (ids and times only; a new session counts
     under `<tool>:new@<folder>`): exit 1 naming the limit; otherwise this send is counted.
4. The run uses `launch.env` with `LLMPRO_SEND_HOPS` set to hops plus one, the session's folder (or
   `--folder`), `start_new_session=True`, a timeout of `[sessions] send_timeout` (default 900 seconds),
   and the host file check from 17. The answer is the tool's stdout, printed to stdout as it comes. On
   timeout llmpro sends SIGTERM to the process group, then SIGKILL to it after 5 seconds, and exits 1.
5. Exit 0 when the tool exited 0, else 1; the tool's own status is in JSON `exit` and on stderr.
6. After the run, llmpro reads which model the tool used (as under "Facts"). When `--model` was given and
   differs: `warning: asked for <model>, <tool> used <model>` on stderr, and in JSON `warnings`.
7. A remote target runs `remote.command(m, ['send', target, '-', '--hops', str(hops)] + rest)` with the
   text on stdin, where `rest` holds `--inside` when the caller is inside a session, and `--folder`,
   `--model`, `-n` and `--json` when given. It passes stdout, stderr and the exit status through. `--hops`
   and `--inside` are hidden options.
8. A new session's id comes from the tool's JSON output when it has one (as under "Facts"); otherwise it is
   the one record created after the start in that folder. With none or several, nothing is recorded and
   llmpro says `could not tell which session was created; find it with llmpro resume`. A known id is
   added with `seen.add` and printed on stderr.
9. `--json`: the envelope with `command: "send"`, `target`, `machine`, `model`, `answer`, `exit`,
   `warnings`. `-n` prints the channel and argv, takes no lock and runs nothing.
10. `send` is not an MCP tool. `send` and `send.json` are added to `CAPABILITIES`.
11. The README gains a short "Live sessions" section (listing, continuing, watching, sending, the limits,
    what is never done), and the skill tells an assistant when to use `llmpro send` and that it must not
    send to ask for approvals.

## Tests

Fake `claude`, `copilot` and `opencode` in `tools_dir` that record argv, stdin, cwd and environment and
print an answer: each channel's argv; text on stdin or after `--`; text `--yolo` never becomes a flag; no
permission flag ever appears (scan argv for `allow`, `yolo`, `dangerously`, `bypass`, `--permission`); an
open session refused; hops from the variable and from `--hops 0` with the variable at 2; a local-only
session refused from inside a session and allowed without one, also for `tool:new --folder` and for a
remote row with `local_only`; a `VSCODE_*` variable alone does not count as inside; `--inside` and the
other options forwarded to the remote; the rate limit across calls; the lock held
by another process; the model warning; a remote send through the fake `ssh` with quoted words; `-n`;
`--json`; a timeout ends the whole process group (a fake tool that starts a `sleep` child).

## Facts (recorded by the integrator before this wave, S10.5, S10.9, S10.10)

To be filled in.
