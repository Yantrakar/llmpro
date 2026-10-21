"""Open assistant sessions on this machine: one Row per session, in the shared states (DECISIONS S1 to S4).

Each tool has an adapter (claude.py, copilot.py, opencode.py) that reads the tool's own records and writes
nothing. Everything about a process comes from `_proc` and the other small readers below, which look under
PROC, so a test can point them at a fake /proc. Nothing here reads `environ`, a `.key` file or a credential.
"""
import dataclasses
import glob
import json
import os
import re
import shlex
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


# --- naming a session ---

class TargetError(Exception):
    """A target that names no session, or more than one. The message says which."""


SHORT_TOOLS = {'claude': 'claude-code', 'claude-code': 'claude-code', 'copilot': 'copilot-cli',
               'copilot-cli': 'copilot-cli', 'opencode': 'opencode'}
MIN_PREFIX = 6


def split_target(target, machines=()):
    """-> (tool or None, key, machine or None) for `[tool:]key[@machine]`. A word before the first colon is a
    tool only when it is a tool name; a word after the last @ is a machine only when it is one of `machines`
    (so a session name may hold a colon or an @)."""
    tool, key, machine = None, target, None
    head, colon, rest = target.partition(':')
    if colon and head in SHORT_TOOLS:
        tool, key = SHORT_TOOLS[head], rest
    front, at, back = key.rpartition('@')
    if at and front and back in machines:
        key, machine = front, back
    return tool, key, machine


def full_target(row):
    return f'{row.tool}:{row.id}@{clean(row.machine)}'


def _first_cwd(path):
    for text in head_lines(path):
        try:
            obj = json.loads(text)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get('cwd'), str):
            return clean(obj['cwd'])
    return ''


def on_disk(key, tool=None):
    """-> rows for sessions of this machine that the tools' own records name, not open now. The folder of a
    Claude session is the first cwd in its transcript, of a Copilot session `context.cwd` of session.start."""
    if not ID.fullmatch(key):
        return []  # nothing else may reach a glob pattern
    home = cfg.home()
    out = []

    def add(kind, sid, folder, path):
        try:
            when = int(os.stat(path).st_mtime)
        except OSError:
            when = None
        out.append(Row(kind, cfg.MACHINE, sid, '', folder, 'unknown', when, '', None, '', '', is_local(folder)))
    if tool in (None, 'claude-code'):
        for path in sorted(glob.glob(os.path.join(home, '.claude', 'projects', '*', key + '*.jsonl'))):
            sid = os.path.basename(path)[:-len('.jsonl')]
            if ID.fullmatch(sid):
                try:
                    add('claude-code', sid, _first_cwd(path), path)
                except OSError:
                    pass
    if tool in (None, 'copilot-cli'):
        from . import copilot
        for path in sorted(glob.glob(os.path.join(home, '.copilot', 'session-state', key + '*', 'events.jsonl'))):
            sid = os.path.basename(os.path.dirname(path))
            if ID.fullmatch(sid):
                try:
                    add('copilot-cli', sid, clean(copilot.start_of(path)[0]), path)
                except OSError:
                    pass
    return out


def _match(key, rows):
    """The first kind of match that has any: an exact id, an exact name, an id prefix of MIN_PREFIX or more."""
    kinds = (lambda r: r.id == key, lambda r: bool(key) and r.name == key,
             lambda r: len(key) >= MIN_PREFIX and r.id.startswith(key))
    for kind in kinds:
        hits, keys = [], set()
        for r in rows:
            if kind(r) and (r.tool, r.machine, r.id) not in keys:
                keys.add((r.tool, r.machine, r.id))
                hits.append(r)
        if hits:
            return hits
    return []


def resolve(target, rows, shown=None):
    """-> the one Row that `target` names. `rows` are the sessions open now. A key that matches none of them is
    looked up among the sessions llmpro has seen and in the tools' own records (those rows have no pid).
    `shown` is the list just shown at the prompt; only then is a number a line of it. TargetError otherwise."""
    from . import seen
    names = {r.machine for r in rows} | {cfg.MACHINE} | {e['machine'] for e in seen.load()}
    names |= {m.name for m in cfg.machines(cfg.STORE)} if cfg.STORE else set()
    tool, key, machine = split_target(target, names)
    if shown is not None and tool is None and machine is None and key.isdigit():
        n = int(key)
        if not 1 <= n <= len(shown):
            raise TargetError(f'there is no line {key} in the list')
        if shown[n - 1].state == 'not-readable' or not shown[n - 1].id:
            raise TargetError(f'line {key} is not a session that can be continued')
        return shown[n - 1]
    if not key:
        raise TargetError('the target names no session')

    def wanted(r):
        return ID.fullmatch(r.id or '') and (tool is None or r.tool == tool) and (machine is None or r.machine == machine)
    pool = [r for r in rows if wanted(r)]
    hits = _match(key, pool)
    if not hits:
        opened = {(r.tool, r.machine, r.id) for r in rows}
        if machine in (None, cfg.MACHINE):
            closed = seen.lookup(key, exclude=opened) + [r for r in on_disk(key, tool) if (r.tool, r.machine, r.id) not in opened]
        else:
            closed = seen.lookup(key, exclude=opened)
        pool += [r for r in closed if wanted(r)]
        hits = _match(key, [r for r in pool if r.pid is None])
    if len(hits) > 1:
        lines = [f'  {full_target(r)}' + (f' ({clean(r.name)})' if r.name else '') for r in hits]
        raise TargetError(f'{clean(key)!r} matches more than one session; name one of these:\n' + '\n'.join(lines))
    if hits:
        return hits[0]
    if 0 < len(key) < MIN_PREFIX and any(r.id.startswith(key) for r in pool):
        raise TargetError(f'{clean(key)!r} is too short to be the start of an id; use at least {MIN_PREFIX} '
                          f'characters, or the whole name')
    hint = ''
    back = key.rpartition('@')[2]
    if '@' in key and back and back not in names:
        hint = f' ({clean(back)} is not a machine in [machines])'
    raise TargetError(f'no session matches {clean(key)!r}{hint}')


# --- the command ---

def _interactive():
    return sys.stdin.isatty() and sys.stdout.isatty()


def _listing(args, store):
    """-> (rows, errors) as `llmpro resume` lists them."""
    if args.local or (args.machine == cfg.MACHINE):
        return local_rows(line=not args.no_line), []
    if not args.json and sys.stderr.isatty():
        asked = [m for m in cfg.machines(store) if not m.error]
        if asked:
            print(f'asking {len(asked)} machines...', file=sys.stderr)
    return all_rows(machine=args.machine, line=not args.no_line)


def command(args, store):
    """llmpro resume [-n] [-y] [--json] [--local] [--machine NAME] [--no-line] [TARGET]: the list of open
    sessions, or continue one."""
    from .. import jsonout
    if not supported():
        print('live sessions need Linux', file=sys.stderr)
        return 2
    if args.watch:
        print('llmpro resume: --watch comes in a later version', file=sys.stderr)
        return 2
    cfg.configure(store, required=False)
    if args.target:
        return resume_target(args, store)
    while True:
        rows, errors = _listing(args, store)
        if args.json:
            jsonout.emit('resume', {'format': FORMAT, 'machine': cfg.MACHINE, 'rows': [r.as_dict() for r in rows],
                                    'errors': errors})
            return 0
        rows = rows + [dataclasses.replace(bad('-', e['machine'], '', e['error']), name='-') for e in errors]
        if not rows:
            print('no open sessions', file=sys.stderr)
            return 0
        print('\n'.join(render(rows, color=use_color())))
        if args.dry_run or not _interactive():
            return 0
        from . import seen
        try:
            seen.record(rows)
        except OSError:
            pass
        try:
            answer = input('Continue which? (number or name, Enter to quit) ').strip()
        except EOFError:
            return 0
        if not answer:
            return 0
        try:
            row = resolve(answer, rows, shown=rows)
        except TargetError as exc:
            print(str(exc), file=sys.stderr)
            continue
        act(row, args, store)


def act(row, args, store):
    """Continue one resolved row here or on its machine, or say why not. -> an exit status."""
    from . import launch, remote
    why = launch.refusal(row)
    if why:
        print(why, file=sys.stderr)
        return 1
    if row.machine == cfg.MACHINE:
        if args.dry_run:
            try:
                argv = launch.command(row)
            except launch.Error as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(f'would continue {row.tool} {launch.label(row)}\nmachine: {cfg.MACHINE}\nfolder: {row.folder}\n'
                  f'run: {" ".join(shlex.quote(w) for w in argv)}')
            return 0
        return launch.run(row)
    m = remote.machine_named(store, row.machine)
    if m is None or m.error:
        print(m.error if m else f'{clean(row.machine)} is not a machine in [machines]', file=sys.stderr)
        return 1
    return act_remote(m, f'{row.tool}:{row.id}', args, folder=row.folder)


def act_remote(m, target, args, folder=''):
    from . import launch
    try:
        argv = launch.remote_argv(m, target)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.dry_run:
        print(f'would continue {clean(target)} on {m.name}\nmachine: {m.name}\nfolder: {folder or "(on that machine)"}\n'
              f'run: {" ".join(shlex.quote(w) for w in argv)}')
        return 0
    return launch.run_remote(m, target)


def resume_target(args, store):
    """llmpro resume TARGET: name the session, then continue it here or on its machine."""
    machines = cfg.machines(store)
    names = {m.name for m in machines} | {cfg.MACHINE}
    tool, key, machine = split_target(args.target, names)
    if machine and machine != cfg.MACHINE:
        from . import remote
        m = remote.machine_named(store, machine)
        if m is None or m.error:
            print(m.error if m else f'{machine} is not a machine in [machines]', file=sys.stderr)
            return 2
        return act_remote(m, args.target[:len(args.target) - len(machine) - 1], args)
    errors = []
    if args.local or machine == cfg.MACHINE or args.machine == cfg.MACHINE:
        rows = local_rows(line=False)
    else:
        rows, errors = all_rows(machine=args.machine, line=False)
    try:
        row = resolve(args.target, rows)
    except TargetError as exc:
        for e in errors:
            print(f'warning: {e["error"]}', file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2
    return act(row, args, store)
