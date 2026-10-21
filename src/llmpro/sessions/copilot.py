"""Copilot CLI: a live `copilot` process that holds ~/.copilot/session-state/<id>/inuse.<pid>.hold, or that
was started with --resume <id>. The state is the last event in events.jsonl. Checked against CHECKED."""
import glob
import json
import os
import re

from .. import sessions as S
from .. import store as cfg

TOOL = 'copilot-cli'
CHECKED = ('1.0.100', '1.0.101')
BOOKKEEPING = ('session.usage_checkpoint', 'session.usage_record', 'session.info', 'session.warning',
               'session.model_change', 'session.permissions_changed', 'session.context_changed',
               'session.binary_asset', 'session.background_tasks_changed', 'system.notification', 'assistant.idle')
REPLY = ('assistant.turn_end', 'abort', 'session.error', 'session.resume', 'session.start')
WORKING = ('user.message', 'assistant.turn_start', 'assistant.message', 'tool.execution_start',
           'tool.execution_complete', 'permission.completed', 'human_response.recorded', 'system.message',
           'session.compaction_start', 'session.compaction_complete', 'session.truncation')


def _bookkeeping(kind):
    return kind in BOOKKEEPING or kind.startswith('model.')


def _working(kind):
    return kind in WORKING or kind.startswith(('subagent.', 'skill.'))


def _events(lines):
    """Parsed events, newest first. A line that is not JSON (a half-written last line) is skipped."""
    for text in reversed(lines):
        try:
            obj = json.loads(text)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get('type'), str):
            yield obj


def _data(event):
    d = event.get('data')
    return d if isinstance(d, dict) else {}


def state_of(path):
    """-> (state, since, intention) from the end of events.jsonl."""
    for event in _events(S.tail_lines(path)):
        kind = event['type']
        if _bookkeeping(kind):
            continue
        when = S.parse_time(event.get('timestamp'))
        if kind == 'permission.requested':
            intention = _data(event).get('intention', event.get('intention'))
            return 'needs-approval', when, intention if isinstance(intention, str) else ''
        if kind in REPLY:
            return 'needs-reply', when, ''
        if _working(kind):
            return 'working', when, ''
        return 'unknown', when, ''
    return 'unknown', None, ''


def start_of(path):
    """-> (folder, version) from session.start, read from the start of the log, or ('', '')."""
    for text in S.head_lines(path):
        try:
            obj = json.loads(text)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get('type') == 'session.start':
            d = _data(obj)
            cwd = (d.get('context') or {}).get('cwd') if isinstance(d.get('context'), dict) else None
            version = d.get('copilotVersion')
            return (cwd if isinstance(cwd, str) else ''), (version if isinstance(version, str) else '')
    return '', ''


def _resume_id(words):
    """The id (or prefix) after --resume or in --resume=ID, or ''. A bare --resume is Copilot's own picker."""
    for i, w in enumerate(words):
        if w.startswith('--resume='):
            return w[len('--resume='):]
        if w == '--resume':
            nxt = words[i + 1] if i + 1 < len(words) else ''
            return '' if nxt.startswith('-') else nxt
    return ''


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return -1


def rows(machine, line=True):
    state_dir = os.path.join(cfg.home(), '.copilot', 'session-state')
    held = {}  # pid -> folders that hold a hook file for it
    for hold in glob.glob(os.path.join(state_dir, '*', 'inuse.*.hold')):
        m = re.fullmatch(r'inuse\.(\d+)\.hold', os.path.basename(hold))
        if m:
            held.setdefault(int(m.group(1)), []).append(os.path.dirname(hold))
    out = []
    for pid in S._pids():
        info = S._proc(pid)
        if info is None or info[0] != os.getuid() or info[1] != 'copilot':
            continue
        try:
            folder = _session_folder(pid, held.get(pid, []), state_dir)
            if folder is not None:
                out.append(_row(machine, pid, folder, line))
        except Exception as exc:
            out.append(S.bad(TOOL, machine, '', f'a Copilot session could not be read: {type(exc).__name__}'))
    return out


def _session_folder(pid, folders, state_dir):
    """The session folder of this process: the held folder whose events.jsonl changed last, else the one
    --resume names (an id or a prefix of one), else None."""
    with_log = [f for f in folders if os.path.isfile(os.path.join(f, 'events.jsonl'))]
    if with_log:
        return max(with_log, key=lambda f: _mtime(os.path.join(f, 'events.jsonl')))
    wanted = _resume_id(S._cmdline(pid))
    if not wanted or not re.fullmatch(r'[A-Za-z0-9-]+', wanted):
        return None
    matches = [f for f in glob.glob(os.path.join(state_dir, wanted + '*'))
               if os.path.isfile(os.path.join(f, 'events.jsonl'))]
    return matches[0] if len(matches) == 1 else None


def _row(machine, pid, folder, line):
    sid = os.path.basename(folder)
    log = os.path.join(folder, 'events.jsonl')
    holds = glob.glob(os.path.join(folder, f'inuse.{pid}.hold'))
    paths = (holds[0] if holds else None, log)
    if not S.ID.fullmatch(sid):
        return S.bad(TOOL, machine, '', 'the session folder name has characters that are not allowed', paths=paths)
    cwd, version = start_of(log)
    if not cwd:
        return S.bad(TOOL, machine, sid, 'session.start does not name a folder', paths=paths)
    state, since, intention = state_of(log)
    if since is None:
        since = int(_mtime(log)) if _mtime(log) >= 0 else None
    local = S.is_local(cwd)
    text = S.make_line(intention) if line and not local and state == 'needs-approval' else ''
    return S.Row(TOOL, machine, sid, '', S.clean(cwd), state, since, text, pid, S.where_of(pid), S.clean(version),
                 local, '', paths)
