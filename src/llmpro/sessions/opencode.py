"""opencode: its server owns every session, so every answer comes from `opencode api <operation>`. llmpro
never calls the server's port, and never reads opencode's database or credential. Checked against CHECKED."""
import json
import os
import subprocess

from .. import sessions as S
from .. import tools

TOOL = 'opencode'
CHECKED = ('2.0.30',)
TIMEOUT = 3


class ApiError(Exception):
    pass


def api(exe, operation, **params):
    """-> the parsed JSON that `opencode api OPERATION [--param key=value]...` prints. ApiError on a failure."""
    argv = [exe, 'api', operation]
    for key, value in params.items():
        argv += ['--param', f'{key}={value}']
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ApiError(f'opencode api {operation} did not answer in {TIMEOUT} seconds') from None
    except OSError:
        raise ApiError(f'opencode api {operation} could not be started') from None
    if done.returncode != 0:
        raise ApiError(f'opencode api {operation} failed with exit status {done.returncode}')
    try:
        return json.loads(done.stdout)
    except ValueError:
        raise ApiError(f'opencode api {operation} did not print JSON') from None


def _data(obj):
    return obj.get('data', obj) if isinstance(obj, dict) else obj


def _clients():
    """-> [(pid, session id or '', folder)] for the live opencode client processes of this user."""
    found = []
    for pid in S._pids():
        info = S._proc(pid)
        if info is None or info[0] != os.getuid() or info[1] != 'opencode':
            continue
        words = S._cmdline(pid)
        if 'serve' in words[1:]:
            continue
        sid = ''
        for i, w in enumerate(words):
            if w in ('-s', '--session') and i + 1 < len(words):
                sid = words[i + 1]
            elif w.startswith('--session='):
                sid = w[len('--session='):]
        found.append((pid, sid, S._cwd(pid)))
    return found


def _permission_line(item):
    if not isinstance(item, dict):
        return ''
    action = item.get('action') if isinstance(item.get('action'), str) else ''
    res = item.get('resources', item.get('resource'))
    first = res[0] if isinstance(res, list) and res else res
    return f'{action} {first}'.strip() if isinstance(first, str) else action


def rows(machine, line=True):
    exe = tools.which('opencode')
    if not exe:
        return []
    try:
        return _rows(exe, machine, line)
    except ApiError as exc:
        return [S.bad(TOOL, machine, '', str(exc))]


def _rows(exe, machine, line):
    data = _data(api(exe, 'session.active'))
    active = set(data) if isinstance(data, dict) else set()
    wanted = {sid: {'active': True, 'client': None} for sid in sorted(active)}
    listed = None
    for pid, sid, folder in _clients():
        if not sid:
            if listed is None:
                got = _data(api(exe, 'session.list'))
                listed = [s for s in got if isinstance(s, dict)] if isinstance(got, list) else []
            same = [s for s in listed if isinstance(s.get('location'), dict) and s['location'].get('directory') == folder
                    and isinstance(s.get('id'), str)]
            same.sort(key=lambda s: S.parse_time((s.get('time') or {}).get('updated')) or 0, reverse=True)
            sid = same[0]['id'] if same else ''
        if sid:
            wanted.setdefault(sid, {'active': sid in active, 'client': None})['client'] = pid
    try:
        version = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=TIMEOUT,
                                 stdin=subprocess.DEVNULL).stdout.strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        version = ''
    out = []
    for sid, how in wanted.items():
        if not S.ID.fullmatch(sid):
            out.append(S.bad(TOOL, machine, '', 'the session id has characters that are not allowed'))
            continue
        try:
            out.append(_row(exe, machine, sid, how, version, line))
        except ApiError as exc:
            out.append(S.bad(TOOL, machine, sid, str(exc)))
    return out


def _row(exe, machine, sid, how, version, line):
    info = _data(api(exe, 'session.get', sessionID=sid))
    if not isinstance(info, dict) or not isinstance(info.get('location'), dict) \
            or not isinstance(info['location'].get('directory'), str):
        return S.bad(TOOL, machine, sid, 'the session has no folder')
    folder = info['location']['directory']
    when = info.get('time') if isinstance(info.get('time'), dict) else {}
    since = S.parse_time(when.get('idle') if when.get('idle') is not None else when.get('updated'))
    asks = _data(api(exe, 'session.permission.list', sessionID=sid))
    asks = asks if isinstance(asks, list) else []
    if asks:
        state = 'needs-approval'
    else:
        state = 'working' if how['active'] else 'needs-reply'
    local = S.is_local(folder)
    text = S.make_line(_permission_line(asks[0])) if asks and line and not local else ''
    title = info.get('title')
    return S.Row(TOOL, machine, sid, S.make_name(title) if isinstance(title, str) else '', S.clean(folder), state,
                 since, text, None, S.where_of(how['client']) if how['client'] else '', S.clean(version), local)
