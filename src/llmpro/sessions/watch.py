"""`llmpro resume --watch`: the list kept current in the terminal, and a desktop notice when a session starts
waiting for the user (DECISIONS S1, S4, S5).

The screen is redrawn every `[sessions] refresh` seconds. Input is read a line at a time with `select`, so there
is no raw mode and no curses. At its start the watch opens one shared ssh login per machine, one after another
(ssh ControlMaster, as `sync.Hub` does), and closes them when it ends, however it ends. A machine that is still
being asked is not asked again; its last rows stay on screen, marked with the time of that answer.

The clock, the sleep, the key reader, the output, the row sources and the notice sender are parameters of `run`,
so a test runs a few refreshes without a terminal or real time. Every string shown or sent goes through `clean`.
"""
import contextlib
import dataclasses
import io
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

from .. import store as cfg
from .. import tools
from . import FORMAT, TargetError, bad, clean, render, resolve, sort, use_color

CLEAR = '\x1b[2J\x1b[H'
PROMPT = 'number or name + Enter to continue, q + Enter to quit'
WAITING = ('needs-approval', 'needs-reply')
NOTICE_GAP = 60  # seconds between two notices for one row
ASK_TIMEOUT = 10
MAX_CYCLES = None  # a test sets a number so the command ends by itself


def hhmmss(t):
    return time.strftime('%H:%M:%S', time.localtime(t))


def terminal():
    return sys.stdin.isatty() and sys.stdout.isatty()


# --- reading a line ---

class Keys:
    """Reads one line at a time from standard input, waiting at most `timeout` seconds (forever for None).
    -> the line without its newline, None when time ran out, EOFError at the end of the input."""
    def __init__(self):
        self.buffer = b''

    def __call__(self, timeout):
        fd = sys.stdin.fileno()
        while b'\n' not in self.buffer:
            ready, _, _ = select.select([fd], [], [], None if timeout is None else max(0, timeout))
            if not ready:
                return None
            data = os.read(fd, 4096)
            if not data:
                if self.buffer:
                    line, self.buffer = self.buffer, b''
                    return line.decode(errors='replace').strip('\r')
                raise EOFError
            self.buffer += data
        line, _, self.buffer = self.buffer.partition(b'\n')
        return line.decode(errors='replace').strip('\r')


# --- the shared ssh logins ---

class Logins:
    """One ssh master per machine in one fresh folder (mode 700, from tempfile.mkdtemp). `control` maps a
    machine name to the ControlPath its asks use; `failed` maps a name to why its login did not work."""
    def __init__(self):
        self.folder = ''
        self.control = {}
        self.failed = {}
        self.targets = {}

    def open(self, machines, say, interactive):
        ssh = tools.which('ssh')
        for m in machines:
            if not ssh:
                continue  # no login to share; remote.ask says that ssh is missing
            if not self.folder:
                self.folder = tempfile.mkdtemp()
            path = f'{self.folder}/%C'
            say(f'logging in to {clean(m.name)} ({clean(m.ssh)})')
            argv = [ssh, '-fN', '-o', 'ControlMaster=yes', '-o', f'ControlPath={path}', '-o', 'ConnectTimeout=10']
            if not interactive:
                argv += ['-o', 'BatchMode=yes']
            try:
                code = subprocess.run(argv + ['--', m.ssh]).returncode
            except OSError as exc:
                self.failed[m.name] = f'{m.name}: cannot run ssh ({type(exc).__name__})'
                continue
            if code != 0:
                self.failed[m.name] = (f'{m.name}: cannot log in (ssh exited {code}); check that `ssh {clean(m.ssh)}` '
                                       f'works')
                continue
            self.control[m.name] = path
            self.targets[m.name] = m.ssh

    def close(self):
        ssh = tools.which('ssh')
        for name, path in list(self.control.items()):
            if ssh:
                try:
                    subprocess.run([ssh, '-o', f'ControlPath={path}', '-O', 'exit', '--', self.targets[name]],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=ASK_TIMEOUT)
                except (OSError, subprocess.SubprocessError):
                    pass
        self.control = {}
        if self.folder:
            shutil.rmtree(self.folder, ignore_errors=True)
            self.folder = ''


# --- asking a machine without waiting for it ---

class Job:
    """One ask in a thread of its own. The thread is a daemon: a stuck ask never holds up the exit."""
    def __init__(self, ask, machine, control, clock):
        self.machine, self.result, self.finished = machine, None, None
        self.done = threading.Event()

        def work():
            try:
                self.result = ask(machine, ASK_TIMEOUT, control)
            except Exception as exc:  # an ask should not raise; one that does is an error for its machine
                self.result = ([], f'{machine.name}: could not be asked ({type(exc).__name__})')
            self.finished = clock()
            self.done.set()
        threading.Thread(target=work, daemon=True).start()


# --- notices ---

class Notices:
    """Decides which rows get a notice. A machine's first answer only seeds what is known."""
    def __init__(self, enabled, send, clock):
        self.enabled, self.send, self.clock = enabled, send, clock
        self.known = {}  # (tool, machine, id) -> state
        self.seeded = set()
        self.sent = {}  # (tool, machine, id) -> time of the last notice

    @staticmethod
    def text(row):
        what = 'needs approval' if row.state == 'needs-approval' else 'needs a reply'
        if row.local_only:
            return clean(f'{row.tool} on {row.machine}: {what}')
        who = clean(row.name) or clean(row.id)[:8]
        return clean(f'{row.tool} {who} on {row.machine}: {what}')

    def update(self, answers):
        """`answers` maps a machine name to its rows, for the machines that answered this time."""
        now = self.clock()
        for machine, rows in answers.items():
            first = machine not in self.seeded
            self.seeded.add(machine)
            present = set()
            for r in rows:
                if not r.id:
                    continue
                key = (r.tool, machine, r.id)
                present.add(key)
                before = self.known.get(key)
                self.known[key] = r.state
                if first or before == r.state or r.state not in WAITING:
                    continue
                if self.enabled and now - self.sent.get(key, now - NOTICE_GAP - 1) >= NOTICE_GAP:
                    self.sent[key] = now
                    self.send('llmpro', self.text(r))
            for key in [k for k in self.known if k[1] == machine and k not in present]:
                del self.known[key]


# --- one refresh ---

class Watch:
    def __init__(self, args, store, clock, ask, local, logins):
        self.args, self.store, self.clock, self.ask, self.local, self.logins = args, store, clock, ask, local, logins
        self.jobs = {}  # machine name -> running Job
        self.last = {}  # machine name -> (rows, error, finished)
        self.fixed = []  # errors that do not change: [{"machine", "error"}]
        self.machines = []
        self.use_local = True
        self.choose_machines()

    def choose_machines(self):
        wanted, own = self.args.machine, cfg.MACHINE
        if self.args.local or wanted == own:
            return
        every = cfg.machines(self.store)
        if wanted:
            self.use_local = False
            if wanted not in [m.name for m in every]:
                self.fixed.append({'machine': wanted, 'error': f'{wanted}: no such machine in [machines]'})
            every = [m for m in every if m.name == wanted]
        for m in every:
            if m.error:
                self.fixed.append({'machine': m.name, 'error': m.error})
        self.machines = [m for m in every if not m.error]

    def login(self, say, interactive):
        self.logins.open(self.machines, say, interactive)
        self.machines = [m for m in self.machines if m.name not in self.logins.failed]

    def collect(self):
        for name, job in list(self.jobs.items()):
            if job.done.is_set():
                rows, error = job.result
                self.last[name] = (rows, error, job.finished)
                del self.jobs[name]

    def refresh(self, grace):
        """-> (rows, errors, stale, fresh): the sorted rows; [{"machine", "error"}]; {machine: time of its last
        answer} for the machines still being asked; {machine: rows} for those that answered cleanly."""
        self.collect()
        started = []
        for m in self.machines:
            if m.name not in self.jobs:
                self.jobs[m.name] = Job(self.ask, m, self.logins.control.get(m.name), self.clock)
                started.append(self.jobs[m.name])
        rows = list(self.local(line=not self.args.no_line)) if self.use_local else []
        deadline = time.monotonic() + grace
        for job in started:
            job.done.wait(max(0.0, deadline - time.monotonic()))
        self.collect()
        errors = list(self.fixed)
        errors += [{'machine': n, 'error': e} for n, e in self.logins.failed.items()]
        stale, fresh = {}, {}
        if self.use_local:
            fresh[cfg.MACHINE] = list(rows)
        for m in self.machines:
            got = self.last.get(m.name)
            if m.name in self.jobs:
                if got is None:
                    errors.append({'machine': m.name, 'error': f'{m.name}: still asking'})
                else:
                    stale[m.name] = got[2]
                    rows += got[0]
                    if got[1]:
                        errors.append({'machine': m.name, 'error': got[1]})
                continue
            if got is None:
                continue
            rows += got[0]
            if got[1]:
                errors.append({'machine': m.name, 'error': got[1]})
            if got[0] or not got[1]:
                fresh[m.name] = got[0]
        return sort(rows), errors, stale, fresh


# --- drawing ---

def screen(rows, errors, stale, now, message, color=False):
    """-> the text lines of one screen."""
    shown = rows + [dataclasses.replace(bad('-', e['machine'], '', e['error']), name='-') for e in errors]
    lines = render(shown, color=color, now=now)
    for i, r in enumerate(rows, 1):
        if r.machine in stale:
            lines[i] += f' (as of {hhmmss(stale[r.machine])})'
    if not shown:
        lines.append('no open sessions')
    lines += ['', f'updated {hhmmss(now)}']
    lines += [clean(m) for m in message]
    lines.append(PROMPT)
    return shown, lines


def choose(answer, shown, args, store):
    """Continue the session `answer` names, with the terminal to itself. -> (message lines, wait). Anything the
    run printed on stderr is kept for the message, because the next redraw would clear it."""
    from . import act, seen
    try:
        row = resolve(answer, shown, shown=shown)
    except TargetError as exc:
        return [m for m in str(exc).splitlines()], False
    try:
        seen.record(shown)
    except OSError:
        pass
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        status = act(row, args, store)
    said = [ln for ln in captured.getvalue().splitlines() if ln.strip()]
    who = clean(row.name) or clean(row.id)[:8]
    return said + ([f'{row.tool} {who} ended with status {status}'] if status else []), bool(said)


# --- the loop ---

def run(args, store, *, clock=None, sleep=None, read=None, out=None, ask=None, local=None, send=None,
        max_cycles=None, grace=2.0):
    """-> the exit status. The ssh logins are closed and their folder removed however this ends."""
    from . import local_rows, remote
    from .. import jsonout, notice
    clock = clock or time.time
    sleep = sleep or time.sleep
    out = out or sys.stdout
    ask = ask or remote.ask
    local = local or local_rows
    send = send or notice.notify
    max_cycles = max_cycles if max_cycles is not None else MAX_CYCLES
    conf = cfg.sessions_config(store)
    refresh = conf['refresh']
    logins = Logins()
    notices = Notices(conf['notify'], send, clock)
    old = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            def term(signum, frame):
                raise SystemExit(1)
            old[sig] = signal.signal(sig, term)
    except ValueError:  # not the main thread
        pass
    watch = Watch(args, store, clock, ask, local, logins)

    def say(msg):
        print(msg, file=sys.stderr, flush=True)
    try:
        watch.login(say, interactive=not args.json)
        read = read or Keys()
        message, last, cycles = [], None, 0
        while True:
            cycles += 1
            sessions_begin()
            rows, errors, stale, fresh = watch.refresh(grace)
            notices.update(fresh)
            if args.json:
                body = {'format': FORMAT, 'machine': cfg.MACHINE, 'rows': [r.as_dict() for r in rows],
                        'errors': errors}
                if body != last:
                    jsonout.emit('resume', body)
                    sys.stdout.flush()
                    last = body
                if max_cycles is not None and cycles >= max_cycles:
                    return 0
                sleep(refresh)
                continue
            shown, lines = screen(rows, errors, stale, clock(), message, color=use_color())
            out.write(CLEAR + '\n'.join(lines) + '\n')
            out.flush()
            message = []
            if max_cycles is not None and cycles >= max_cycles:
                return 0
            try:
                answer = read(refresh)
            except EOFError:
                return 0
            if answer is None or not answer.strip():
                continue
            answer = answer.strip()
            if answer == 'q':
                return 0
            message, wait = choose(answer, shown, args, store)
            if wait:
                out.write('\n'.join(clean(m) for m in message) + '\npress Enter to go back\n')
                out.flush()
                message = []
                try:
                    read(None)
                except EOFError:
                    return 0
    except KeyboardInterrupt:
        return 130
    finally:
        logins.close()
        for sig, handler in old.items():
            signal.signal(sig, handler)


def sessions_begin():
    from . import begin
    begin()


def command(args, store):
    """llmpro resume --watch [--json] [--local] [--machine NAME] [--no-line]."""
    if args.target or args.dry_run:
        print('llmpro resume: --watch takes no target and no -n', file=sys.stderr)
        return 2
    if not args.json and not terminal():
        print('llmpro resume: --watch needs a terminal; in a script use --json --watch', file=sys.stderr)
        return 2
    cfg.configure(store, required=False)
    return run(args, store)
