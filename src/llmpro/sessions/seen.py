"""Sessions llmpro has seen, so a session that is not open now can still be named in `llmpro resume TARGET`.

The file is $XDG_STATE_HOME/llmpro/seen.json (mode 0600, at most MAX entries, the oldest dropped). It keeps the
tool, id, name, folder, machine and last seen time of each session, and never the line of content. A session
in a local-only folder, and a row that could not be read, is not kept. A file that is damaged is ignored.
"""
import json
import os
import time

from .. import store as cfg
from . import ID, TOOLS, Row, clean

MAX = 500
REFRESH = 3600  # a session already kept is rewritten for its time only when the entry is this old
KEYS = ('tool', 'id', 'name', 'folder', 'machine', 'seen')


def state_dir():
    """$XDG_STATE_HOME/llmpro, read each time so a test can change it."""
    base = os.environ.get('XDG_STATE_HOME') or os.path.join(cfg.home(), '.local', 'state')
    return os.path.join(base, 'llmpro')


def path():
    return os.path.join(state_dir(), 'seen.json')


def _entry(row, now):
    return {'tool': row.tool, 'id': row.id, 'name': clean(row.name), 'folder': clean(row.folder),
            'machine': clean(row.machine), 'seen': int(now)}


def _keep(row):
    return (isinstance(row, Row) and row.tool in TOOLS and ID.fullmatch(row.id or '') and row.state != 'not-readable'
            and not row.local_only)


def _valid(e):
    return (isinstance(e, dict) and all(k in e for k in KEYS) and e['tool'] in TOOLS
            and isinstance(e['id'], str) and ID.fullmatch(e['id'])
            and all(isinstance(e[k], str) for k in ('name', 'folder', 'machine'))
            and isinstance(e['seen'], int) and not isinstance(e['seen'], bool))


def load():
    """-> the entries, oldest first. [] for a file that is missing or damaged; a bad entry is dropped."""
    try:
        with open(path(), encoding='utf-8') as fh:
            data = json.load(fh)
        items = data['sessions']
        if not isinstance(items, list):
            return []
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [e for e in items if _valid(e)]


def _same(a, b):
    return all(a[k] == b[k] for k in ('tool', 'id', 'machine'))


def save(entries):
    folder = state_dir()
    os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = os.path.join(folder, f'seen.json.{os.getpid()}.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump({'sessions': entries}, fh)
        os.replace(tmp, path())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record(rows, now=None):
    """Remember these rows. Writes only when something changed."""
    now = time.time() if now is None else now
    entries = load()
    changed = False
    for row in rows:
        if not _keep(row):
            continue
        new = _entry(row, now)
        old = next((e for e in entries if _same(e, new)), None)
        if old is not None:
            if all(old[k] == new[k] for k in ('name', 'folder')) and new['seen'] - old['seen'] < REFRESH:
                continue
            entries.remove(old)
        entries.append(new)
        changed = True
    if changed:
        save(entries[-MAX:])


def add(row, now=None):
    """Remember one session that llmpro has just continued."""
    try:
        record([row], now)
    except OSError:
        pass  # a state folder that cannot be written must not hide the tool's exit status


def lookup(key, exclude=()):
    """-> rows for the kept sessions whose id or name is `key` or whose id starts with it, leaving out
    those in `exclude` (a set of (tool, machine, id), the ones open now). The rows have no pid."""
    from . import is_local
    out = []
    for e in load():
        if (e['tool'], e['machine'], e['id']) in exclude:
            continue
        if key == e['id'] or key == e['name'] or (key and e['id'].startswith(key)):
            out.append(Row(e['tool'], e['machine'], e['id'], e['name'], e['folder'], 'unknown', e['seen'], '', None,
                           '', '', is_local(e['folder']) if e['machine'] == cfg.MACHINE else False))
    return out
