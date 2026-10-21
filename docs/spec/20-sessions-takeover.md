# 20 Take over an idle session

Wave 14. Branch `spec/20-sessions-takeover`. Test file `tests/test_sessions_takeover.py`.

## Goal

`llmpro resume TARGET` for a session open in another terminal closes it there and continues it here, only
while it is safely idle (S7, S11). This is the last package of version 1 and can be left out without
harming the others.

## May edit

`src/llmpro/sessions/__init__.py`, `src/llmpro/sessions/launch.py`, `src/llmpro/cli.py`, `README.md` (one
paragraph in "Live sessions").

## Creates

`src/llmpro/sessions/takeover.py`, `tests/test_sessions_takeover.py`.

## Uses

From 15 to 19: `sessions.Row` (`paths`), `sessions._proc(pid)`, the adapters' `rows`, `launch.lock(row)`,
`launch.run(row, lock=None)`, `launch.remote_argv(m, target)`. `os.pidfd_open`, `signal.pidfd_send_signal`,
`select.poll`. Fixtures `home`, `store`.

## Requirements

1. `takeover.check(row, now)` -> list of reasons it is refused, empty when it may go:
   - the state is not `needs-reply`: `it is <state>; only a session waiting for a reply is taken over`;
   - in `needs-reply` for less than 10 seconds, or its record or log changed in the last 10 seconds;
   - its terminal, from `takeover._tty(pid)` (the target of `/proc/<pid>/fd/0`), is not a `/dev/pts/N`: `it is not in a terminal llmpro can check`;
   - that terminal was used in the last 60 seconds (its access or modify time): `someone typed in it <n>s
     ago`;
   - it has child processes (`/proc/<pid>/task/*/children`): `it has N child processes (<their
     programs>); they would end with it`;
   - no live, verified pid (S2);
   - its `where` is `container`: `it runs in a container`;
   - it is an opencode session: never taken over, it is continued as in 17 (Facts in 15).
2. `takeover.run(row, yes)`:
   1. take `launch.lock(row)` (`Busy`: refuse) and keep it until the session runs here;
   2. show `Close <tool> <name> (pid N, <where>, <machine>, idle <t>, no child processes) and continue it
      here? Anything typed there and not sent is lost. [y/N]` and ask once; `-y` answers yes; no terminal and no `-y`: exit 2 naming both;
   3. `pidfd_open(pid)`; send SIGSTOP; check again (item 1, and the mtime and size of each of `Row.paths`); if anything
      changed, send SIGCONT only and refuse;
   4. print `to continue it elsewhere later: llmpro resume <target>` (there is no undo for a closed
      process), then send SIGHUP and SIGCONT through the pidfd;
   5. wait up to 5 seconds for the pidfd to become readable (the process exited). A record or lock whose
      pid is gone counts as gone. If the process is still there: `pid N did not exit; nothing else was
      done`, exit 1. Never SIGKILL;
   6. `launch.run(row, lock=<the lock from step 1>)`.
3. Any refusal exits 1 and prints every reason from `check`.
4. In `llmpro resume TARGET`, an open target goes to `takeover.run` instead of 17's refusal.
5. `-n` prints the check result and the plan, takes no lock and sends no signal.
6. A remote session's takeover is never done from here: it is `launch.remote_argv` as in 17, and the
   remote llmpro does all of it there.

## Tests

The process side is a real child process started by the test (a `sleep`, presented through
`sessions._proc` as the tool), so signals are real and harmless: refused states, the 10 second rule (fake
clock), a non-pts fd 0, a recent terminal use (`takeover._tty` replaced by a file with a fresh mtime), children present, the
lock held, a container row. The happy path: the child has the slave side of `os.openpty()` as its stdin, shows state `T` in `/proc/<pid>/stat` after SIGSTOP, gets SIGHUP and
SIGCONT and exits, then the fake tool runs with the same lock. A child that ignores SIGHUP: llmpro stops
after 5 seconds (fake clock) and sends no SIGKILL. A change while stopped: only SIGCONT. No terminal and no
`-y`: exit 2. A refusal exits 1.

## Facts (recorded by the integrator before this wave, S10.6)

Checked on 2026-10-21.

- S10.6: `KillUserProcesses` is not set in `/etc/systemd/logind.conf` on either machine checked, so the
  default (no) applies: logging out does not end a user's processes. Linger was on for one machine and
  off for the other. Takeover does not depend on either.
- From 17's facts: Claude exits within about a second of SIGHUP (status 129) and removes its record;
  Copilot exits within a second and writes `session.shutdown`. Both lose a half-typed message, hence the
  sentence in the question. The 5 second wait is enough for both.
