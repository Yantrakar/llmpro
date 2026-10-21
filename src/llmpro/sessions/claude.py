"""Claude Code: ~/.claude/sessions/<pid>.json. Claude does not remove the record of a process that died, so
each record's process is checked. Checked against the versions in CHECKED."""
import glob
import json
import os
import re

from .. import sessions as S
from .. import store as cfg

TOOL = 'claude-code'
CHECKED = ('2.1.293', '2.1.305')
STATUS = {'idle': 'needs-reply', 'busy': 'working', 'waiting': 'needs-approval'}
PROGRAMS = ('claude', 'node')


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _live_pid(pid, domain, start):
    """-> (pid here, in a container) when the record's process is live, ours and Claude's; ('skip') if not.
    A bad pidDomain raises ValueError."""
    here, container = pid, False
    if domain is not None:
        m = re.fullmatch(r'linux:([^:]*):pid:\[(\d+)\]', domain)
        if not m:
            raise ValueError('pidDomain is in a form this llmpro does not know')
        machine_id, inode = m.groups()
        if machine_id:
            try:
                with open(S.MACHINE_ID_FILE, encoding='utf-8') as fh:
                    mine = fh.read().strip()
            except OSError:
                mine = machine_id
            if mine != machine_id:
                return None, False
        if inode != S.own_pid_ns():
            here, container = S._ns_pid(inode, pid), True
            if here is None:
                return None, False
    info = S._proc(here)
    if info is None or info[0] != os.getuid() or info[1] not in PROGRAMS:
        return None, False
    if start is None and info[1] != 'claude':
        return None, False  # a node process proves nothing without procStart
    if start is not None and str(start) != str(info[2]):
        return None, False
    return here, container


def _last_text(path):
    """The start of the last assistant text in a transcript, read from the end."""
    for text in reversed(S.tail_lines(path)):
        try:
            obj = json.loads(text)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get('type') != 'assistant':
            continue
        content = (obj.get('message') or {}).get('content')
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [b.get('text') for b in content if isinstance(b, dict) and b.get('type') == 'text'
                     and isinstance(b.get('text'), str)]
            if parts:
                return parts[-1]
    return ''


def rows(machine, line=True):
    out = []
    home = cfg.home()
    for path in sorted(glob.glob(os.path.join(home, '.claude', 'sessions', '*.json'))):
        try:
            out.extend(_record(path, home, machine, line))
        except Exception as exc:
            out.append(S.bad(TOOL, machine, '', f'the record {os.path.basename(path)} could not be read: '
                             f'{type(exc).__name__}', paths=(path, None)))
    return out


def _record(path, home, machine, line):
    paths = (path, None)
    try:
        with open(path, encoding='utf-8') as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return [S.bad(TOOL, machine, '', 'the record is not valid JSON', paths=paths)]
    if not isinstance(rec, dict):
        return [S.bad(TOOL, machine, '', 'the record is not an object', paths=paths)]
    pid = rec.get('pid')
    if not _is_int(pid):
        return [S.bad(TOOL, machine, '', 'the record has no valid pid', paths=paths)]
    domain = rec.get('pidDomain')
    if domain is not None and not isinstance(domain, str):
        return [S.bad(TOOL, machine, '', 'pidDomain has the wrong type', paths=paths)]
    try:
        here, container = _live_pid(pid, domain, rec.get('procStart'))
    except ValueError as exc:
        return [S.bad(TOOL, machine, '', str(exc), paths=paths)]
    if here is None:
        return []
    sid = rec.get('sessionId')
    for key, kind in (('sessionId', str), ('cwd', str), ('status', str)):
        if not isinstance(rec.get(key), kind):
            why = 'is missing' if key not in rec else 'has the wrong type'
            return [S.bad(TOOL, machine, sid, f'{key} {why}', paths=paths)]
    if not S.ID.fullmatch(sid):
        return [S.bad(TOOL, machine, '', 'the session id has characters that are not allowed', paths=paths)]
    state = STATUS.get(rec['status'], 'unknown')
    folder = rec['cwd']
    local = S.is_local(folder)
    text = ''
    transcript = None
    hits = glob.glob(os.path.join(home, '.claude', 'projects', '*', sid + '.jsonl'))
    if hits:
        transcript = sorted(hits)[0]
    if line and not local:
        if state == 'needs-approval':
            text = 'permission prompt'
        elif state == 'needs-reply' and transcript:
            try:
                text = S.make_line(_last_text(transcript))
            except OSError:
                text = ''
    name = rec.get('name')
    version = rec.get('version')
    return [S.Row(TOOL, machine, sid, S.make_name(name) if isinstance(name, str) else '', S.clean(folder), state,
                  S.parse_time(rec.get('statusUpdatedAt')), '' if local else text, here,
                  'container' if container else S.where_of(here), S.clean(version) if isinstance(version, str) else '',
                  local, '', (path, transcript))]
