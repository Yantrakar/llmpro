"""Open assistant sessions on this machine: one Row per session, in the shared states (DECISIONS S1 to S4).

Each tool has an adapter (claude.py, copilot.py, opencode.py) that reads the tool's own records and writes
nothing. Everything about a process comes from `_proc` and the other small readers below, which look under
PROC, so a test can point them at a fake /proc. Nothing here reads `environ`, a `.key` file or a credential.
"""
import dataclasses
import os
import re
import sys
import time

from .. import store as cfg

FORMAT = 'llmpro-sessions.v1'
STATES = ('needs-approval', 'needs-reply', 'working', 'unknown', 'not-readable')
ID = re.compile(r'[A-Za-z0-9_-]+')  # opencode ids carry an underscore
TOOLS = ('claude-code', 'copilot-cli', 'opencode')
PROC = '/proc'  # tests point this at a fake tree
MACHINE_ID_FILE = '/etc/machine-id'
LINE_WIDTH = 120
TAIL = 256 * 1024  # how much of the end of a log is read for a state or a line
HEAD = 64 * 1024  # how much of the start of a log is read for the folder and version


@dataclasses.dataclass
class Row:
    tool: str
    machine: str
    id: str
    name: str
    folder: str
    state: str
    since: int | None
    line: str
    pid: int | None
    where: str
    version: str
    local_only: bool
    error: str = ''
    paths: tuple | None = None  # (record or lock file, event log or transcript); not part of the JSON form

    def as_dict(self):
        d = dataclasses.asdict(self)
        del d['paths']
        return d

    @classmethod
    def from_dict(cls, d):
        """Read the JSON form back. ValueError for anything that is not a row of this version."""
        if not isinstance(d, dict):
            raise ValueError('a row must be an object')
        kinds = {'str': (str,), 'int?': (int, type(None)), 'bool': (bool,)}
        values = {}
        for f in dataclasses.fields(cls):
            if f.name == 'paths':
                continue
            if f.name not in d:
                raise ValueError(f'row has no {f.name}')
            kind = 'int?' if f.name in ('since', 'pid') else 'bool' if f.name == 'local_only' else 'str'
            v = d[f.name]
            if not isinstance(v, kinds[kind]) or (kind == 'int?' and isinstance(v, bool)):
                raise ValueError(f'row {f.name} has the wrong type')
            values[f.name] = v
        return cls(**values)


def sort(rows):
    """Waiting first (STATES order), then the longest in that state."""
    def key(r):
        rank = STATES.index(r.state) if r.state in STATES else len(STATES)
        return (rank, r.since is None, r.since or 0)
    return sorted(rows, key=key)


_ESC = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?'  # OSC
                  r'|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]?'  # CSI
                  r'|\x1b[ -/]*[0-~]?')  # other two-character sequences
_CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f]')


def clean(text, width=None):
    """Remove control characters and whole ESC sequences, join lines with a space, cut to `width`."""
    text = _ESC.sub('', '' if text is None else str(text))
    text = ' '.join(text.splitlines())
    text = _CONTROL.sub(lambda m: ' ' if m.group(0) == '\t' else '', text)
    return text[:width] if width is not None else text


def supported():
    return os.path.exists(os.path.join(PROC, 'self')) and hasattr(os, 'pidfd_open')


# --- /proc ---

def _pids():
    try:
        return sorted(int(n) for n in os.listdir(PROC) if n.isdigit())
    except OSError:
        return []


def _proc(pid):
    """-> (uid, program, start_time) of a process, or None. program is the base name of the first word of
    cmdline, or comm only when cmdline is empty (comm of Copilot and of VS Code's server is MainThread).
    start_time is field 22 of stat, in clock ticks since boot. Never reads environ."""
    base = os.path.join(PROC, str(pid))
    try:
        uid = os.stat(base).st_uid
        with open(os.path.join(base, 'cmdline'), 'rb') as fh:
            raw = fh.read()
        first = raw.split(b'\0')[0].decode('utf-8', 'replace')
        if first:
            program = os.path.basename(first)
        else:
            with open(os.path.join(base, 'comm'), encoding='utf-8', errors='replace') as fh:
                program = fh.read().strip()
        fields = _stat_fields(pid)
        return uid, program, int(fields[19])
    except (OSError, ValueError, IndexError, TypeError):
        return None


def _stat_fields(pid):
    """The fields of /proc/<pid>/stat after the program name, so index 0 is field 3 (the state)."""
    with open(os.path.join(PROC, str(pid), 'stat'), encoding='utf-8', errors='replace') as fh:
        text = fh.read()
    return text[text.rindex(')') + 1:].split()


def _ppid(pid):
    try:
        return int(_stat_fields(pid)[1])
    except (OSError, ValueError, IndexError):
        return 0


def _cmdline(pid):
    """The words of the command line. For matching only; callers never print them."""
    try:
        with open(os.path.join(PROC, str(pid), 'cmdline'), 'rb') as fh:
            raw = fh.read()
    except OSError:
        return []
    return [w.decode('utf-8', 'replace') for w in raw.split(b'\0') if w]


def _cwd(pid):
    try:
        return os.readlink(os.path.join(PROC, str(pid), 'cwd'))
    except OSError:
        return ''


def _ns_pid(inode, pid):
    """The pid here of a process that has pid `pid` in the pid namespace `inode` (a session in a
    container), or None. Scans PROC/*/ns/pid and matches the last field of NSpid."""
    want = f'pid:[{inode}]'
    for p in _pids():
        try:
            if os.readlink(os.path.join(PROC, str(p), 'ns', 'pid')) != want:
                continue
            with open(os.path.join(PROC, str(p), 'status'), encoding='utf-8', errors='replace') as fh:
                for row in fh:
                    if row.startswith('NSpid:'):
                        if row.split()[-1] == str(pid):
                            return p
                        break
        except (OSError, IndexError):
            continue
    return None


def own_pid_ns():
    """The inode of this process's pid namespace as text, or ''."""
    try:
        link = os.readlink(os.path.join(PROC, 'self', 'ns', 'pid'))
    except OSError:
        return ''
    m = re.fullmatch(r'pid:\[(\d+)\]', link)
    return m.group(1) if m else ''


def where_of(pid):
    """vscode, ssh or terminal, from the programs of the process's parents."""
    seen = set()
    found = set()
    p = _ppid(pid)
    while p > 1 and p not in seen and len(seen) < 64:
        seen.add(p)
        info = _proc(p)
        if info:
            found.add(info[1])
        p = _ppid(p)
    if found & {'code', 'code-insiders'}:
        return 'vscode'
    return 'ssh' if 'sshd' in found else 'terminal'


# --- reading logs ---

def tail_lines(path, size=TAIL):
    """The last `size` bytes of a file as whole lines, oldest first. A last line without a newline may be
    half written; callers skip a line they cannot parse."""
    with open(path, 'rb') as fh:
        end = fh.seek(0, os.SEEK_END)
        start = max(0, end - size)
        fh.seek(start)
        data = fh.read()
    lines = data.split(b'\n')
    if start > 0:
        lines = lines[1:]  # the first one is cut
    return [ln.decode('utf-8', 'replace') for ln in lines if ln.strip()]


def head_lines(path, size=HEAD):
    with open(path, 'rb') as fh:
        data = fh.read(size)
    lines = data.split(b'\n')
    if len(data) == size:
        lines = lines[:-1]  # the last one may be cut
    return [ln.decode('utf-8', 'replace') for ln in lines if ln.strip()]


def parse_time(value):
    """An ISO time or a number (seconds or milliseconds) as epoch seconds, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 1e11 else int(value)
    if isinstance(value, str):
        import datetime
        try:
            when = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        return int(when.timestamp())
    return None


# --- what a row shows ---

_LOCAL = {}


def begin():
    """Start a listing: forget which folders were asked about."""
    _LOCAL.clear()


def is_local(folder):
    """store.is_local_project, asked once per folder per listing."""
    if folder not in _LOCAL:
        _LOCAL[folder] = bool(folder) and bool(cfg.is_local_project(folder))
    return _LOCAL[folder]


def make_line(text):
    from ..redact import redact
    return clean(redact(text or ''), LINE_WIDTH)


def make_name(text):
    from ..redact import redact
    return clean(redact(text or ''))


def bad(tool, machine, id, error, folder='', paths=None):
    """The row for a record this llmpro cannot read. An id that does not match is left out."""
    return Row(tool, machine, id if isinstance(id, str) and ID.fullmatch(id) else '', '', clean(folder), 'not-readable', None, '', None,
               '', '', False, clean(error), paths)


def local_rows(line=True, machine=None):
    """The three adapters' rows together, sorted."""
    from . import claude, copilot, opencode
    machine = machine or cfg.MACHINE
    begin()
    rows = []
    for adapter in (claude, copilot, opencode):
        try:
            rows.extend(adapter.rows(machine, line=line))
        except Exception as exc:  # an adapter should not raise; one that does must not hide the others
            rows.append(bad(adapter.TOOL, machine, '', f'{adapter.TOOL} could not be read: {type(exc).__name__}'))
    return sort(rows)


def all_rows(machine=None, control=None, line=True, timeout=10):
    """-> (rows, errors): this machine's rows and those of every machine in [machines], asked at once, merged
    and sorted. errors is [{"machine", "error"}]. `machine` limits it to one name (this machine's own name
    too). `control` maps a machine name to a ssh ControlPath."""
    from . import remote
    asked = [] if machine == cfg.MACHINE else cfg.machines(cfg.STORE)
    errors = []
    if machine and machine != cfg.MACHINE:
        if machine not in [m.name for m in asked]:
            return [], [{'machine': machine, 'error': f'{machine}: no such machine in [machines]'}]
        asked = [m for m in asked if m.name == machine]
    with_error = [m for m in asked if m.error]
    errors += [{'machine': m.name, 'error': m.error} for m in with_error]
    ok = [m for m in asked if not m.error]
    # remote answers are awaited in threads while this machine's rows are read here
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(remote.ask_all, ok, timeout, control)
        rows = [] if machine and machine != cfg.MACHINE else local_rows(line=line)
        answers = waiting.result()
    for m, got, error in answers:
        rows.extend(got)
        if error:
            errors.append({'machine': m.name, 'error': error})
    return sort(rows), errors


# --- the command ---

def age(since, now=None):
    if since is None:
        return '-'
    secs = max(0, int((now if now is not None else time.time()) - since))
    if secs < 60:
        return f'{secs}s'
    if secs < 3600:
        return f'{secs // 60}m'
    if secs < 48 * 3600:
        return f'{secs // 3600}h'
    return f'{secs // 86400}d'


def tilde(folder):
    home = cfg.home().rstrip(os.sep)
    if home and folder == home:
        return '~'
    if home and folder.startswith(home + os.sep):
        return '~' + folder[len(home):]
    return folder


COLORS = {'needs-approval': '31', 'needs-reply': '33', 'working': '32', 'unknown': '2', 'not-readable': '35'}


def use_color():
    return sys.stdout.isatty() and not os.environ.get('NO_COLOR')


def render(rows, color=False, now=None):
    """The human list as text lines: a header, then one line per row."""
    table = []
    for i, r in enumerate(rows, 1):
        who = clean(r.name) or r.id[:8]
        text = clean(r.error) if r.state == 'not-readable' else clean(r.line)
        table.append([str(i), r.state, r.tool, f'{who}@{clean(r.machine)}', clean(tilde(r.folder)), age(r.since, now),
                      text])
    head = ['#', 'STATE', 'TOOL', 'SESSION', 'FOLDER', 'AGE', 'LINE']
    widths = [max(len(head[c]), *(len(t[c]) for t in table)) if table else len(head[c]) for c in range(6)]

    def fmt(cells, paint=False):
        out = []
        for c in range(6):
            cell = cells[c].ljust(widths[c])
            if paint and c == 1 and cells[c] in COLORS:
                cell = f'\x1b[{COLORS[cells[c]]}m{cell}\x1b[0m'
            out.append(cell)
        return ('  '.join(out) + '  ' + cells[6]).rstrip()
    return [fmt(head)] + [fmt(t, color) for t in table]


def command(args, store):
    """llmpro resume [-n] [-y] [--json] [--local] [--machine NAME] [--no-line]: the list on this machine."""
    from .. import jsonout
    if not supported():
        print('live sessions need Linux', file=sys.stderr)
        return 2
    for form, used in (('a target', args.target), ('--watch', args.watch)):
        if used:
            print(f'llmpro resume: {form} comes in a later version', file=sys.stderr)
            return 2
    cfg.configure(store, required=False)
    errors = []
    if args.local or (args.machine == cfg.MACHINE):
        rows = local_rows(line=not args.no_line)
    else:
        if not args.json and sys.stderr.isatty():
            asked = [m for m in cfg.machines(store) if not m.error]
            if asked:
                print(f'asking {len(asked)} machines...', file=sys.stderr)
        rows, errors = all_rows(machine=args.machine, line=not args.no_line)
    if args.json:
        jsonout.emit('resume', {'format': FORMAT, 'machine': cfg.MACHINE, 'rows': [r.as_dict() for r in rows],
                                'errors': errors})
        return 0
    rows = rows + [dataclasses.replace(bad('-', e['machine'], '', e['error']), name='-') for e in errors]
    if not rows:
        print('no open sessions', file=sys.stderr)
        return 0
    print('\n'.join(render(rows, color=use_color())))
    if args.dry_run or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return 0
    try:
        answer = input('Continue which? (number or name, Enter to quit) ').strip()
    except EOFError:
        return 0
    if not answer:
        return 0
    print('continuing a session comes in a later version', file=sys.stderr)
    return 2
