"""Give one message to a session and print its answer (DECISIONS S8, S11).

The message goes to the tool on stdin and never becomes an argument. No flag that allows tools or skips a
permission is ever added: a headless turn that needs a permission is refused by the tool itself. The tool runs
in its own process group, so a timeout ends it and everything it started. A send holds the session's lock while
it runs, and the count of sends in the last hour is read and changed under that lock.
"""
import contextlib
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time

from .. import store as cfg
from .. import tools
from . import ID, Row, TargetError, clean, launch, seen

MAX_TEXT = 64 * 1024
GRACE = 5  # seconds between SIGTERM and SIGKILL
HOUR = 3600
DEFAULTS = {'max_hops': 2, 'sends_per_hour': 20, 'send_timeout': 900}
MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/@+-]*')
PROGRAMS = {'claude-code': 'claude', 'copilot-cli': 'copilot', 'opencode': 'opencode'}
# Variables that widen what a tool may do without asking (S8). `resume` keeps them, as the user set them for
# their own sessions; a send never carries them. Copilot CLI 1.0.101 documents COPILOT_ALLOW_ALL (all tools
# without asking; "true" also trusts the folder). Claude Code and opencode keep permissions in settings files.
WIDENING = ('COPILOT_ALLOW_ALL',)


class Refused(Exception):
    """A send that must not run. `code` is the exit status."""
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


# --- the channel ---

def new_row(tool, folder):
    return Row(tool, cfg.MACHINE, '', '', folder, 'unknown', None, '', None, '', '', False)


def target_of(row):
    return f'{row.tool}:{row.id or "new"}@{clean(row.machine)}'


def refusal(row):
    """Raise Refused for a session that must not be sent to: a container, or one open in a terminal (opencode
    may be open: it has its own channel)."""
    if row.where == 'container':
        raise Refused(f'{target_of(row)} runs in a container; send to it there')
    if row.pid is not None and row.tool != 'opencode':
        raise Refused(f'{target_of(row)} is open in {clean(row.where) or "a terminal"} on {clean(row.machine)}; '
                      f'llmpro does not type into an open session')


def channel(row, model=''):
    """-> the argv that gives one message to this session (the text is read from stdin). A row with no id is a
    new session. Refused (Refused) for a session open in a terminal, except opencode, and for a container."""
    if row.tool not in PROGRAMS:
        raise Refused(f'{clean(row.tool)} is not a tool llmpro can send to', 2)
    refusal(row)
    if row.id and (not ID.fullmatch(row.id) or row.id.startswith('-')):
        raise Refused(f'{clean(row.id)[:40]!r} is not a session id llmpro can send to', 2)
    if model and (not MODEL.fullmatch(model)):
        raise Refused(f'{clean(model)[:40]!r} is not a model name llmpro will pass on', 2)
    exe = tools.which(PROGRAMS[row.tool])
    if not exe:
        raise Refused(f'{PROGRAMS[row.tool]} is not installed here (or not on the path)')
    if row.tool == 'claude-code':
        argv = [exe, '-p', '--output-format', 'json'] + (['--resume', row.id] if row.id else [])
    elif row.tool == 'copilot-cli':
        argv = [exe, '-s', '--output-format', 'json'] + (['--resume', row.id] if row.id else [])
    else:
        argv = [exe, 'run', '--format', 'json'] + (['-s', row.id] if row.id else [])
    if model:
        argv += ['-m' if row.tool == 'opencode' else '--model', model]
    return argv


# --- the answer ---

def _objects(text):
    """-> the JSON objects in the output: the whole text as one, else one per line."""
    try:
        whole = json.loads(text)
        if isinstance(whole, dict):
            return [whole]
    except ValueError:
        pass
    out = []
    for line in text.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _dig(obj, *keys):
    for k in keys:
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj


def _text(v):
    return v if isinstance(v, str) else ''


def parse(tool, stdout):
    """-> dict(answer, session, model, ok) read from the tool's JSON. ok is False when the output shows a run
    that did not happen (a Copilot run with no result line)."""
    objs = _objects(stdout)
    got = {'answer': '', 'session': '', 'model': '', 'ok': True}
    if tool == 'claude-code':
        last = next((o for o in reversed(objs) if 'result' in o or 'session_id' in o), {})
        got['answer'] = _text(last.get('result'))
        got['session'] = _text(last.get('session_id'))
        usage = last.get('modelUsage')
        got['model'] = ', '.join(str(k) for k in usage) if isinstance(usage, dict) else ''
        got['ok'] = bool(last)
    elif tool == 'copilot-cli':
        results = [o for o in objs if o.get('type') == 'result']
        messages = [o for o in objs if o.get('type') == 'assistant.message']
        if messages:
            got['answer'] = _text(_dig(messages[-1], 'data', 'content'))
            got['model'] = _text(_dig(messages[-1], 'data', 'model'))
        if results:
            got['session'] = _text(results[-1].get('sessionId'))
        got['ok'] = bool(results)
    else:
        parts = [_text(_dig(o, 'part', 'text')) for o in objs if o.get('type') == 'text']
        got['answer'] = ''.join(parts)
        got['session'] = next((o['sessionID'] for o in objs if isinstance(o.get('sessionID'), str)), '')
        got['ok'] = bool(objs)
    if not ID.fullmatch(got['session']):
        got['session'] = ''
    return got


def _model_name(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        provider = value.get('providerID') or value.get('provider')
        name = value.get('modelID') or value.get('id') or value.get('model')
        if isinstance(name, str):
            return f'{provider}/{name}' if isinstance(provider, str) else name
    return ''


def opencode_model(session, folder, environ):
    """-> the model opencode says the session uses, or ''."""
    exe = tools.which('opencode')
    if not exe or not ID.fullmatch(session or ''):
        return ''
    try:
        r = subprocess.run([exe, 'api', 'session.get', '--param', f'sessionID={session}'], cwd=folder, env=environ,
                           capture_output=True, text=True, errors='replace', stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ''
    for obj in _objects(r.stdout):
        name = _model_name(obj.get('model')) or _model_name(_dig(obj, 'data', 'model'))
        if name:
            return name
    return ''


def differs(asked, used):
    """Is `used` not what `asked` named? An alias such as haiku is part of the full name it stands for."""
    if not asked or not used:
        return False
    names = [u.strip().lower() for u in used.split(',')]
    return not any(asked.lower() == n or asked.lower() in n for n in names)


# --- the counts (S8) ---

def sends_path():
    return os.path.join(seen.state_dir(), 'sends.json')


@contextlib.contextmanager
def _counts_lock():
    folder = seen.state_dir()
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(folder, 'sends.lock'), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def count(key, limit, now=None):
    """Count one send under `key`. Refused when `limit` sends were counted in the last hour. Call it with the
    session's lock held."""
    now = time.time() if now is None else now
    with _counts_lock():
        try:
            with open(sends_path(), encoding='utf-8') as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data = {k: [t for t in v if isinstance(t, (int, float)) and not isinstance(t, bool) and now - t < HOUR]
                for k, v in data.items() if isinstance(k, str) and isinstance(v, list)}
        data = {k: v for k, v in data.items() if v}
        recent = data.get(key, [])
        if len(recent) >= limit:
            raise Refused(f'{clean(key)} already had {limit} sends in the last hour; the limit is sends_per_hour '
                          f'in [sessions]')
        data[key] = recent + [now]
        tmp = sends_path() + f'.{os.getpid()}.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(data, fh)
        os.replace(tmp, sends_path())


# --- running a tool ---

def _group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def spawn(argv, text, cwd=None, environ=None, capture=True, timeout=900):
    """Run argv in its own process group with `text` on stdin. -> (status, stdout text, timed_out). On timeout
    the group gets SIGTERM, then SIGKILL after GRACE seconds."""
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=environ, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE if capture else None, start_new_session=True)
    except OSError as exc:
        print(f'{os.path.basename(argv[0])} could not be started ({type(exc).__name__})', file=sys.stderr)
        return 1, '', False
    timed_out = False
    try:
        out, _ = proc.communicate(text, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _group(proc, signal.SIGTERM)
        try:
            proc.wait(GRACE)
        except subprocess.TimeoutExpired:
            pass
        _group(proc, signal.SIGKILL)
        try:
            out, _ = proc.communicate(timeout=GRACE)
        except subprocess.TimeoutExpired:
            out = b''
    except BaseException:
        _group(proc, signal.SIGTERM)
        raise
    status = proc.returncode
    status = status if status >= 0 else 128 - status
    return status, (out or b'').decode('utf-8', 'replace'), timed_out


# --- the command ---

def _number(conf, key):
    v = conf.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else DEFAULTS[key]


def limits(store):
    conf = cfg.sessions_config(store)
    return {k: _number(conf, k) for k in DEFAULTS}


def read_text(args):
    text = args.text
    if text is None or text == '-':
        if text is None and sys.stdin.isatty():
            raise Refused('give the message as TEXT, or pipe it in', 2)
        text = sys.stdin.read(MAX_TEXT + 1)
    data = text.encode('utf-8')
    if len(data) > MAX_TEXT:
        raise Refused(f'the message is longer than {MAX_TEXT // 1024} KB', 2)
    if not data.strip():
        raise Refused('the message is empty', 2)
    return data


def hops_of(args):
    """-> (hops, inside): the larger of --hops and LLMPRO_SEND_HOPS, and whether the caller is in a session."""
    mine = os.environ.get('LLMPRO_SEND_HOPS')
    try:
        theirs = max(0, int(mine)) if mine is not None else 0
    except ValueError:
        theirs = 0
    inside = mine is not None or bool(args.inside) or any(v in os.environ for v in launch.INSIDE)
    return max(args.hops, theirs), inside


def _fail(args, message, code):
    if args.json:
        from .. import jsonout
        jsonout.emit('send', {'target': clean(args.target), 'error': clean(message)}, ok=False)
    else:
        print(clean(message), file=sys.stderr)
    return code


def command(args, store):
    """llmpro send TARGET [TEXT]: give one message to a session and print its answer."""
    from . import supported
    if not supported():
        print('live sessions need Linux', file=sys.stderr)
        return 2
    cfg.configure(store, required=False)
    try:
        return _send(args, store)
    except Refused as exc:
        return _fail(args, str(exc), exc.code)
    except launch.Busy as exc:
        return _fail(args, str(exc), 1)
    except launch.Error as exc:
        return _fail(args, str(exc), 1)
    except TargetError as exc:
        return _fail(args, str(exc), 2)


def _send(args, store):
    from . import all_rows, local_rows, resolve, split_target
    from . import remote
    lim = limits(store)
    hops, inside = hops_of(args)
    if hops >= lim['max_hops']:
        raise Refused(f'this message would be hop {hops + 1}; the limit is max_hops in [sessions]')
    if args.target.startswith('-'):
        raise Refused('the target must not start with -', 2)
    text = read_text(args)
    machines = cfg.machines(store)
    names = {m.name for m in machines} | {cfg.MACHINE}
    tool, key, machine = split_target(args.target, names)
    new = tool is not None and key == 'new'
    if new and machine in (None, cfg.MACHINE):
        folder = os.path.abspath(args.folder or os.getcwd())
        if inside and cfg.is_local_project(folder):
            raise Refused(f'{clean(args.target)} is in a local-only project; send to it from your own terminal')
        if not os.path.isdir(folder):
            raise Refused(f'{clean(folder)} is not a folder', 2)
        return local(args, store, new_row(tool, folder), text, hops, lim)
    if machine and machine != cfg.MACHINE:
        m = remote.machine_named(store, machine)
        if m is None or m.error:
            raise Refused(m.error if m else f'{clean(machine)} is not a machine in [machines]', 2)
        target = args.target[:len(args.target) - len(machine) - 1]
        if new and not args.folder:
            raise Refused('a new session on another machine needs --folder', 2)
        return send_remote(args, m, target, text, hops, inside, lim)
    if not key:
        raise Refused('the target names no session', 2)
    rows = local_rows(line=False) if machine == cfg.MACHINE else all_rows(line=False)[0]
    row = resolve(args.target, rows)
    if inside and row.local_only:
        raise Refused(f'{clean(args.target)} is in a local-only project; send to it from your own terminal')
    if row.machine != cfg.MACHINE:
        m = remote.machine_named(store, row.machine)
        if m is None or m.error:
            raise Refused(m.error if m else f'{clean(row.machine)} is not a machine in [machines]', 2)
        refusal(row)  # an open session or a container is refused before ssh
        return send_remote(args, m, f'{row.tool}:{row.id}', text, hops, inside, lim)
    return local(args, store, row, text, hops, lim)


def _rest(args, inside):
    rest = ['--inside'] if inside else []
    if args.folder:
        rest += ['--folder', args.folder]
    if args.model:
        rest += ['--model', args.model]
    if args.dry_run:
        rest.append('-n')
    if args.json:
        rest.append('--json')
    return rest


def send_remote(args, m, target, text, hops, inside, lim):
    """Run the send on machine m with the text on ssh's stdin. Its output and status are ours."""
    from . import remote
    try:
        argv = remote.command(m, ['send', target, '-', '--hops', str(hops)] + _rest(args, inside))
    except ValueError as exc:
        raise Refused(str(exc)) from None
    exe = tools.which('ssh')
    if not exe:
        raise Refused(f'{m.name}: ssh is not installed here')
    argv[0] = exe
    status, out, timed_out = spawn(argv, text, timeout=lim['send_timeout'] + 30)
    if out:
        print(_lines(out))  # what the other machine printed is cleaned here too
    if timed_out:
        print(f'{m.name}: no end to the send within {lim["send_timeout"] + 30} seconds; stopped ssh', file=sys.stderr)
        return 1
    return status


def local(args, store, row, text, hops, lim):
    new = not row.id
    model = args.model or ''
    argv = channel(row, model)
    folder = row.folder
    if not new:
        if not folder or not os.path.isdir(folder):
            raise Refused(f'{clean(folder) or target_of(row)} is gone; the session cannot be sent to there')
    if args.dry_run:
        return report(args, row, model, '', 0, [], dry=argv)
    with contextlib.nullcontext() if new else launch.lock(row):
        if not new:
            now = launch.now_open(row)
            if now is not None:
                channel(now, model)  # refused if it is open now
        count(f'{row.tool}:new@{folder}' if new else f'{row.tool}:{row.id}', lim['sends_per_hour'])
        return execute(args, row, argv, text, folder, hops, lim, model)


def execute(args, row, argv, text, folder, hops, lim, model):
    environ = {k: v for k, v in launch.env(os.environ).items() if k not in WIDENING}
    environ['LLMPRO_SEND_HOPS'] = str(hops + 1)
    before = launch.host_snapshot()
    started = time.time()
    status, stdout, timed_out = spawn(argv, text, cwd=folder, environ=environ, timeout=lim['send_timeout'])
    for line in launch.host_report(before, started, row.tool):
        print(line, file=sys.stderr)
    if timed_out:
        print(f'{row.tool} did not finish within {lim["send_timeout"]} seconds; stopped it', file=sys.stderr)
        return report(args, row, model, '', 1, [], used='', status=status)
    got = parse(row.tool, stdout)
    warnings = []
    session = row.id or got['session']
    used = got['model']
    if row.tool == 'opencode' and session:
        used = opencode_model(session, folder, environ) or used
    exit_code = 0 if status == 0 else 1
    if status == 0 and not got['ok']:
        exit_code = 1
        print(f'{row.tool} gave no result; the message may not have been taken (an unknown --model ends like this)',
              file=sys.stderr)
    if status != 0:
        print(f'{row.tool} exited with status {status}', file=sys.stderr)
    if model and used and differs(model, used):
        warnings.append(f'asked for {clean(model)}, {row.tool} used {clean(used)}')
        print(f'warning: {warnings[-1]}', file=sys.stderr)
    if not row.id:
        if session:
            made = Row(row.tool, cfg.MACHINE, session, '', folder, 'unknown', int(time.time()), '', None, '', '',
                       bool(cfg.is_local_project(folder)))
            seen.add(made)
            print(f'session: {row.tool}:{session}', file=sys.stderr)
        else:
            print('could not tell which session was created; find it with llmpro resume', file=sys.stderr)
    return report(args, row, model, got['answer'], exit_code, warnings, used=used, session=session, status=status)


def _lines(text):
    return '\n'.join(clean(line) for line in (text or '').splitlines())


def report(args, row, model, answer, code, warnings, dry=None, used='', session='', status=0):
    """Print the answer, or the JSON envelope, whose `exit` is the tool's own status. -> code."""
    answer = _lines(answer)
    if args.json:
        from .. import jsonout
        body = {'target': clean(args.target), 'machine': clean(row.machine), 'model': clean(used or model),
                'answer': answer, 'exit': status, 'warnings': [clean(w) for w in warnings]}
        if session:
            body['session'] = session
        if dry is not None:
            body.update(dry_run=True, channel=row.tool, argv=[clean(a) for a in dry])
        jsonout.emit('send', body, ok=code == 0)
    elif dry is not None:
        import shlex
        print(f'would send to {target_of(row)}\nmachine: {clean(row.machine)}\nfolder: {clean(row.folder)}\n'
              f'channel: {row.tool}\nrun: {" ".join(shlex.quote(clean(a)) for a in dry)}\n'
              f'the message goes on stdin')
    elif answer:
        print(answer)
    return code
