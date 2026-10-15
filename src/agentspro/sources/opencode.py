"""opencode sessions (~/.local/share/opencode/opencode.db: session, message, part tables)."""
import json
import os
import sqlite3

from .. import store
from ..redact import Conversation, inline_cmd, parse_ts


def db():
    return os.path.join(store.home(), '.local', 'share', 'opencode', 'opencode.db')


class _Source(str):
    """A source 'file' is the database plus a session id; str() is what goes into source_file."""

    def __new__(cls, session_id):
        obj = super().__new__(cls, f'{db()}#session/{session_id}')
        obj.session_id = session_id
        return obj


def source_files():
    if not os.path.exists(db()):
        return []
    con = sqlite3.connect(f'file:{db()}?mode=ro', uri=True)
    ids = [r[0] for r in con.execute('select id from session order by time_created')]
    con.close()
    return [_Source(i) for i in ids]


def _describe(tool, inp):
    inp = inp or {}
    if tool == 'bash':
        return f'shell: {inline_cmd(inp.get("command", ""))}', False
    if tool in ('edit', 'write', 'patch', 'multiedit'):
        path = inp.get('filePath') or inp.get('path') or ''
        new, old = inp.get('newString') or inp.get('content') or '', inp.get('oldString') or ''
        return f'{"write" if tool == "write" else "edit"}: {path} (+{len(new.splitlines())} -{len(old.splitlines())})', True
    if tool == 'read':
        return f'read: {inp.get("filePath", "")}', False
    brief = json.dumps(inp, ensure_ascii=False)
    if len(brief) > 160:
        brief = brief[:160] + ' …'
    return f'{tool}: {brief}', False


def parse(source, titles=None):
    sid = source.session_id
    con = sqlite3.connect(f'file:{db()}?mode=ro', uri=True)
    row = con.execute('select title, directory, version, parent_id, time_created, time_updated from session where id=?',
                      (sid,)).fetchone()
    conv = Conversation('opencode', sid, str(source))
    title, directory, version, parent, created, updated = row
    conv.title, conv.project, conv.tool_version = title or '', directory or '', version or ''
    conv.started, conv.updated = parse_ts(created), parse_ts(updated)
    if parent:
        conv.skip = 'subagent'
    msgs = con.execute('select id, time_created, data from message where session_id=? order by time_created, id', (sid,)).fetchall()
    for mid, mcreated, mdata in msgs:
        m = json.loads(mdata)
        role = m.get('role')
        if m.get('summary') is True or m.get('mode') == 'compaction':
            continue  # compaction summary written by opencode, not a real reply
        dt = parse_ts(mcreated)
        texts = []
        for (pdata,) in con.execute('select data from part where message_id=? order by time_created, id', (mid,)):
            p = json.loads(pdata)
            pt = p.get('type')
            if pt == 'text' and not p.get('synthetic') and not p.get('ignored'):
                texts.append(p.get('text', ''))
            elif pt == 'file':
                texts.append(f'[file: {p.get("filename") or p.get("url", "")}]')
            elif pt == 'tool':
                if texts:
                    conv.msg(role, dt, '\n\n'.join(texts))
                    texts = []
                st = p.get('state') or {}
                summ, is_edit = _describe(p.get('tool', ''), st.get('input'))
                out = None if is_edit else (st.get('output') if st.get('status') != 'error' else f'(error) {st.get("error", "")}')
                conv.tool_call(dt, summ, out)
        if texts:
            conv.msg(role, dt, '\n\n'.join(texts))
    con.close()
    if not conv.skip and conv.user_turns == 0:
        conv.skip = 'empty'
    return conv
