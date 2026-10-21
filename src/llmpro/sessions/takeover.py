"""Take over a session that is open in another terminal on this machine (DECISIONS S7, S11).

Only a session that waits for a reply, has done so for a while, whose terminal has been quiet and that has
no child processes. llmpro holds the process through a pidfd, stops it, looks again, then sends SIGHUP and
SIGCONT, and waits for it to exit. It never sends SIGKILL. Nothing here reads a process's environment.
"""
import dataclasses
import glob
import os
import re
import select
import signal
import sys
import time

from .. import sessions as S
from . import launch

QUIET = 10  # seconds a session has waited, and its record and log unchanged
TYPED = 60  # seconds a terminal must have had no keystroke
WAIT = 5  # seconds the process gets to exit after SIGHUP
PTS = re.compile(r'/dev/pts/\d+')  # a terminal llmpro can check; tests point this at a file
PROGRAMS = {'claude-code': ('claude', 'node'), 'copilot-cli': ('copilot',)}
clock = time.time  # tests replace it


def _tty(pid):
    """-> where the process's standard input points (/proc/<pid>/fd/0), or ''."""
    try:
        return os.readlink(os.path.join(S.PROC, str(pid), 'fd', '0'))
    except OSError:
        return ''


def _verified(row):
    """-> S._proc(pid) when the row's pid is a live process of this user running the tool, else None."""
    if not isinstance(row.pid, int) or isinstance(row.pid, bool):
        return None
    info = S._proc(row.pid)
    if info is None or info[0] != os.getuid() or info[1] not in PROGRAMS.get(row.tool, ()):
        return None
    return info


def _children(pid):
    """-> the programs of the child processes, or None when they cannot be read."""
    files = glob.glob(os.path.join(S.PROC, str(pid), 'task', '*', 'children'))
    if not files:
        return None
    found = []
    for path in files:
        try:
            with open(path, encoding='utf-8') as fh:
                words = fh.read().split()
        except OSError:
            return None
        for word in words:
            info = S._proc(int(word)) if word.isdigit() else None
            found.append(info[1] if info else 'unknown')
    return found


def _ago(now, when):
    return max(0, int(now - when))


def _terminal(pid, now):
    path = _tty(pid)
    if not PTS.fullmatch(path):
        return ['it is not in a terminal llmpro can check']
    try:
        st = os.stat(path)
    except OSError:
        return ['it is not in a terminal llmpro can check']
    ago = _ago(now, max(st.st_atime, st.st_mtime))
    return [f'someone typed in it {ago}s ago'] if ago < TYPED else []


def _kids(pid):
    kids = _children(pid)
    if kids is None:
        return ['its child processes cannot be read']
    if not kids:
        return []
    names = sorted(set(kids))
    shown = ', '.join(names[:5]) + (', ...' if len(names) > 5 else '')
    return [f'it has {len(kids)} child processes ({shown}); they would end with it']


def _recent(row, now):
    for path in row.paths or ():
        try:
            ago = _ago(now, os.stat(path).st_mtime) if path else QUIET
        except OSError:
            continue
        if ago < QUIET:
            return [f'its record or log changed {ago}s ago']
    return []


def check(row, now):
    """-> the reasons the session is not taken over now; an empty list when it may go."""
    if row.tool == 'opencode':
        return ['opencode sessions are never taken over; the server owns them']
    reasons = []
    if row.state != 'needs-reply':
        reasons.append(f'it is {row.state}; only a session waiting for a reply is taken over')
    elif row.since is None or _ago(now, row.since) < QUIET:
        reasons.append(f'it has waited for a reply for less than {QUIET}s')
    reasons += _recent(row, now)
    if row.where == 'container':
        reasons.append('it runs in a container')
    elif _verified(row) is None:
        reasons.append('it has no live process llmpro can verify')
    else:
        reasons += _terminal(row.pid, now) + _kids(row.pid)
    return reasons


def snapshot(row):
    """-> (mtime, size) of each of the row's files, to see a change while the process is stopped."""
    out = []
    for path in row.paths or ():
        try:
            st = os.stat(path) if path else None
        except OSError:
            st = None
        out.append((st.st_mtime_ns, st.st_size) if st else None)
    return out


def refuse(row, reasons):
    """Say why the session stays where it is. -> 1."""
    print(launch.refusal(row), file=sys.stderr)
    print('it was not taken over:', file=sys.stderr)
    for reason in reasons:
        print(f'  {reason}', file=sys.stderr)
    return 1


def plan(row):
    """llmpro resume -n: show the check and the plan. Takes no lock and sends no signal. -> 0, or 1 when refused."""
    reasons = check(row, clock())
    print(f'would take over {row.tool} {launch.label(row)} (pid {row.pid}, {S.clean(row.where)}, {S.clean(row.machine)})')
    if reasons:
        print('check: refused')
        for reason in reasons:
            print(f'  {reason}')
        return 1
    print('check: it may go')
    print('plan: take the lock, ask once, stop the process, check again, send SIGHUP and SIGCONT, wait up to '
          f'{WAIT}s for it to exit, then continue the session here')
    return 0


def _question(row, now):
    idle = S.age(row.since, now)
    return (f'Close {row.tool} {launch.label(row)} (pid {row.pid}, {S.clean(row.where)}, {S.clean(row.machine)}, '
            f'idle {idle}, no child processes) and continue it here? Anything typed there and not sent is lost. [y/N] ')


def _agreed(row, yes):
    """-> True, False, or None when there is no way to ask."""
    if yes:
        return True
    if not S._interactive():
        return None
    try:
        return input(_question(row, clock())).strip().lower() in ('y', 'yes')
    except EOFError:
        return False


def _send(fd, sig):
    """Send a signal through the pidfd. -> False when the process is gone."""
    try:
        signal.pidfd_send_signal(fd, sig)
    except ProcessLookupError:
        return False
    return True


def _exited(fd):
    """Wait up to WAIT seconds for the pidfd to become readable. -> True when the process exited."""
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    end = clock() + WAIT
    while True:
        left = end - clock()
        if poller.poll(max(0.0, min(left, 0.25)) * 1000):
            return True
        if left <= 0:
            return False


def _look(row, start, before):
    """Look again at the stopped process. -> the reasons it must not be closed, empty when it may be."""
    now_row = launch.now_open(row)
    if now_row is None or now_row.pid != row.pid:
        return ['the session is no longer open as it was']
    reasons = check(now_row, clock())
    info = _verified(now_row)
    if info is not None and info[2] != start:
        reasons.append('the process is not the one that was checked')
    if snapshot(now_row) != before:
        reasons.append('its record or log changed while it was being stopped')
    return reasons


def _close(row, start, before):
    """Stop, look again, then close the process. -> 0 when it exited, else an exit status."""
    try:
        fd = os.pidfd_open(row.pid)
    except OSError as exc:
        print(f'pid {row.pid} could not be held ({type(exc).__name__}); nothing was done', file=sys.stderr)
        return 1
    stopped = False
    try:
        if not _send(fd, signal.SIGSTOP):
            return refuse(row, ['the process is gone'])
        stopped = True
        reasons = _look(row, start, before)
        if reasons:
            return refuse(row, reasons)
        print(f'to continue it elsewhere later: llmpro resume {row.tool}:{row.id}')
        sys.stdout.flush()
        _send(fd, signal.SIGHUP)
        _send(fd, signal.SIGCONT)
        stopped = False
        if not _exited(fd):
            print(f'pid {row.pid} did not exit; nothing else was done', file=sys.stderr)
            return 1
        return 0
    finally:
        if stopped:
            _send(fd, signal.SIGCONT)
        os.close(fd)


def _locked(row, yes, held):
    now_row = launch.now_open(row)  # S11: read it again
    if now_row is None or now_row.pid is None:
        return refuse(row, ['it is no longer open as it was listed; look again'])
    row = now_row
    reasons = check(row, clock())
    if reasons:
        return refuse(row, reasons)
    agreed = _agreed(row, yes)
    if agreed is None:
        print('taking over a session needs an answer: there is no terminal to ask in and -y was not given',
              file=sys.stderr)
        return 2
    if not agreed:
        print('not taken over', file=sys.stderr)
        return 1
    info = _verified(row)
    if info is None:
        return refuse(row, ['it has no live process llmpro can verify'])
    status = _close(row, info[2], snapshot(row))
    if status:
        return status
    return launch.run(dataclasses.replace(row, pid=None, where=''), lock=held)


def run(row, yes=False):
    """Close the session where it is open and continue it here. -> an exit status (the tool's own once it runs)."""
    try:
        with launch.lock(row) as held:
            return _locked(row, yes, held)
    except launch.Error as exc:
        print(str(exc), file=sys.stderr)
        return 1
