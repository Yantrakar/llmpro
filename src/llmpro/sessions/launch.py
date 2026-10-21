"""Continue a session in this terminal (DECISIONS S6, S11).

llmpro runs the tool's own resume command as a child, in the session's folder, with the variables that tell a
tool it runs inside another taken out, while it holds the session's lock. Afterwards it says if the tool changed
a file of the editor's settings. It never writes those files. Nothing here reads a process's environment or a
key file, and nothing is printed from the environment.
"""
import contextlib
import fcntl
import hashlib
import os
import re
import shlex
import signal
import subprocess
import sys
import time

from .. import store as cfg
from .. import tools
from . import ID, TOOLS, clean, seen

PROGRAMS = {'claude-code': ('claude', '--resume'), 'copilot-cli': ('copilot', '--resume'), 'opencode': ('opencode', '-s')}

# Variables a tool sets for itself, or for its shell commands, that tell the next tool it runs inside one.
# Each entry says where it was seen. A name ending in _ is a prefix; a name starting with _ is a suffix.
NESTING = (
    'CLAUDECODE',  # Claude Code sets it in every shell command it runs
    'CLAUDE_PID',  # Claude Code: the pid of the session
    'CLAUDE_EFFORT',  # Claude Code: the effort level of the session
    'CLAUDE_CODE_',  # Claude Code: entrypoint, SSE port, and the like
    'AI_AGENT',  # set by agents so other tools can tell
    'COPILOT_',  # Copilot CLI: COPILOT_CLI and the settings it passes on
    'OPENCODE',  # opencode: set in its shell commands
    'OPENCODE_',  # opencode: its other variables
    'VSCODE_',  # VS Code's terminal: IPC sockets, git askpass helpers
    '_VSCODE_SNAP_ORIG',  # VS Code started from a snap saves the old value of each variable under this suffix
    'LLMPRO_',  # llmpro's own; only LLMPRO_SEND_HOPS goes on
)
KEEP = ('LLMPRO_SEND_HOPS',)
GIT_COPILOT = re.compile(r'GIT_CONFIG_COUNT|GIT_CONFIG_(?:KEY|VALUE)_\d+|GIT_OPTIONAL_LOCKS|GIT_TERMINAL_PROMPT')
# Editor settings files a tool must not change (S6). Copilot CLI once wrote into keybindings.json.
HOST_FILES = tuple(os.path.join('.config', app, 'User', name) for app in ('Code', 'Code - Insiders')
                   for name in ('keybindings.json', 'settings.json'))


class Error(Exception):
    """A reason the session is not continued, in words for the person."""


class Busy(Error):
    pass


def _nesting(name):
    if name in KEEP:
        return False
    for entry in NESTING:
        if entry.endswith('_') and name.startswith(entry):
            return True
        if entry.startswith('_') and name.endswith(entry):
            return True
        if name == entry:
            return True
    return False


def env(environ):
    """-> a new environment: `environ` without the nesting variables (a copy; the original is not changed)."""
    out = {k: v for k, v in environ.items() if not _nesting(k)}
    if 'OPENCODE' in environ:
        out.pop('AGENT', None)
    if 'COPILOT_CLI' in environ:
        for k in [k for k in out if GIT_COPILOT.fullmatch(k)]:
            del out[k]
    if environ.get('TERM_PROGRAM') == 'vscode':
        out.pop('TERM_PROGRAM', None)
        out.pop('TERM_PROGRAM_VERSION', None)
    return out


def label(row):
    """How a person names the session in a message."""
    return clean(row.name) or row.id


def command(row):
    """-> the argv that continues the session. Error if the program is not installed."""
    program = PROGRAMS.get(row.tool)
    if not program or not ID.fullmatch(row.id or ''):
        raise Error(f'{label(row)}: not a session this llmpro can continue')
    exe = tools.which(program[0])
    if not exe:
        raise Error(f'{program[0]} is not installed here (or not on the path), so {label(row)} cannot be continued')
    return [exe, program[1], row.id]


def refusal(row):
    """-> why the session must not be continued here, or ''."""
    if row.where == 'container':
        return f'{label(row)} runs in a container; continue it there'
    if row.pid is not None:
        return (f'{label(row)} is open in {clean(row.where) or "a terminal"} on {clean(row.machine)} '
                f'(pid {row.pid}); close it there first')
    return ''


# --- the lock (S11) ---

class Held:
    """What `lock` gives: the path of the lock file. Pass it to `run` to say the lock is already held."""
    def __init__(self, path):
        self.path = path


@contextlib.contextmanager
def hold(row):
    if row.tool not in TOOLS or not isinstance(row.id, str) or not ID.fullmatch(row.id):
        raise Error(f'{clean(str(row.id))[:40]!r} is not a session id llmpro can lock')
    folder = os.path.join(seen.state_dir(), 'live')
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, f'{row.tool}-{row.id}.lock')
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(f'{label(row)} is busy with another llmpro') from None
        yield Held(path)
    finally:
        os.close(fd)  # closing the file releases the lock


lock = hold


# --- the editor's settings files (S6) ---

def host_snapshot():
    """-> {path: sha256 hex or None} for every file in HOST_FILES."""
    out = {}
    for rel in HOST_FILES:
        path = os.path.join(cfg.home(), rel)
        try:
            with open(path, 'rb') as fh:
                out[path] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            out[path] = None
    return out


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def host_report(before, started, tool='the tool'):
    """-> lines for each file that changed since `before`. Writes nothing."""
    now = host_snapshot()
    lines = []
    for path, digest in now.items():
        if before.get(path) == digest:
            continue
        lines.append(f'warning: {path} changed while {tool} ran')
        for suffix in ('.backup', '.bak'):
            backup = path + suffix
            when = _mtime(backup)
            if when is not None and when > started:
                lines.append(f'see the change: diff {shlex.quote(backup)} {shlex.quote(path)}')
                lines.append(f'to put it back: mv -f {shlex.quote(backup)} {shlex.quote(path)}')
                break
    return lines


# --- running ---

def _child(argv, cwd=None, environ=None):
    """Run argv in the foreground of this terminal and -> its exit status. While it runs, SIGINT and SIGQUIT
    go to a handler that does nothing, so the child is not left behind by a Ctrl-C meant for it; the handler
    is not SIG_IGN, which the child would inherit, and a caught signal is reset to the default on exec."""
    saved = {}
    try:
        for sig in (signal.SIGINT, signal.SIGQUIT):
            try:
                saved[sig] = signal.signal(sig, lambda *a: None)
            except ValueError:  # not the main thread
                pass
        try:
            done = subprocess.Popen(argv, cwd=cwd, env=environ)
        except OSError as exc:
            print(f'{os.path.basename(argv[0])} could not be started ({type(exc).__name__})', file=sys.stderr)
            return 1
        while True:
            try:
                status = done.wait()
                break
            except KeyboardInterrupt:
                continue
        return status if status >= 0 else 128 - status
    finally:
        for sig, old in saved.items():
            signal.signal(sig, old)


def remote_argv(m, target):
    """-> the ssh argv that continues `target` (no @machine) on machine m, with a terminal."""
    from . import remote
    if m.error or not m.ssh or m.ssh.startswith('-'):
        raise ValueError(f'{m.name}: not a machine that can be asked')
    return ['ssh', '-t', '--', m.ssh, remote.remote_string(m, ['resume', target])]


def run_remote(m, target):
    """Run the continue on machine m as a child; -> its exit status."""
    argv = remote_argv(m, target)
    exe = tools.which('ssh')
    if not exe:
        print(f'{m.name}: ssh is not installed here', file=sys.stderr)
        return 1
    argv[0] = exe
    return _child(argv)


def _now_open(row):
    """-> the same session read again on this machine, or None when it is not there."""
    from .. import sessions
    for r in sessions.local_rows(line=False):
        if r.tool == row.tool and r.id == row.id:
            return r
    return None


def run(row, lock=None):
    """Continue a session on this machine and -> the tool's exit status (1 when it is refused)."""
    try:
        with contextlib.nullcontext(lock) if lock is not None else hold(row):
            return _run(row)
    except Busy as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Error as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _run(row):
    now = _now_open(row)
    why = refusal(row) or (refusal(now) if now is not None else '')
    if why:
        print(why, file=sys.stderr)
        return 1
    argv = command(row)
    if not row.folder:
        print(f'{label(row)} has no folder; the session cannot be continued', file=sys.stderr)
        return 1
    if not os.path.isdir(row.folder):
        print(f'{row.folder} is gone; the session cannot be continued there', file=sys.stderr)
        return 1
    print(f'continuing {row.tool} {label(row)} in {row.folder}')
    sys.stdout.flush()
    before = host_snapshot()
    started = time.time()
    status = _child(argv, cwd=row.folder, environ=env(os.environ))
    for line in host_report(before, started, row.tool):
        print(line, file=sys.stderr)
    seen.add(row)
    return status
