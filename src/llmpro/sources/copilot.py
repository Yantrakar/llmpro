"""GitHub Copilot Chat in VS Code (workspaceStorage/<hash>/chatSessions, globalStorage/emptyWindowChatSessions)."""
import glob
import json
import os
import re
import urllib.parse

from .. import store
from ..redact import Conversation, inline_cmd, parse_ts


def user_dirs():
    return [os.path.join(store.home(), '.config', app, 'User') for app in ('Code', 'Code - Insiders')]


_ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07')
_MDLINK = re.compile(r'\[([^\]]*)\]\((?:file|vscode-remote)://[^)#]*?([^/)#]*)(?:#[^)]*)?\)')


def source_files():
    out = []
    for u in user_dirs():
        out += glob.glob(os.path.join(u, 'workspaceStorage', '*', 'chatSessions', '*.json'))
        out += glob.glob(os.path.join(u, 'workspaceStorage', '*', 'chatSessions', '*.jsonl'))
        out += glob.glob(os.path.join(u, 'globalStorage', 'emptyWindowChatSessions', '*.json'))
        out += glob.glob(os.path.join(u, 'globalStorage', 'emptyWindowChatSessions', '*.jsonl'))
    return sorted(out)


def _get(obj, path, create=False):
    for k in path:
        if isinstance(obj, list):
            obj = obj[k]
        else:
            if k not in obj or obj[k] is None:
                if not create:
                    return None
                obj[k] = []
            obj = obj[k]
    return obj


def replay(path):
    """Rebuild the session state. kind 0 = initial state, 1 = set path, 2 = splice/append array, 3 = delete."""
    if path.endswith('.json'):
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    state = None
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind = e.get('kind')
            if kind == 0:
                state = e.get('v')
            elif state is None:
                continue
            elif kind == 1:
                *parent, last = e['k']
                container = _get(state, parent, create=True) if parent else state
                if isinstance(container, list) and isinstance(last, int):
                    while len(container) <= last:
                        container.append(None)
                container[last] = e.get('v')
            elif kind == 2:
                arr = _get(state, e['k'], create=True)
                if 'i' in e:
                    del arr[e['i']:]
                arr.extend(e.get('v') or [])
            elif kind == 3:
                *parent, last = e['k']
                container = _get(state, parent) if parent else state
                try:
                    del container[last]
                except (KeyError, IndexError, TypeError):
                    pass
    return state


def workspace_folder(path):
    ws = os.path.join(os.path.dirname(os.path.dirname(path)), 'workspace.json')
    try:
        d = json.load(open(ws, encoding='utf-8'))
    except (OSError, ValueError):
        return '' if 'emptyWindowChatSessions' not in path else '(no workspace)'
    uri = d.get('folder') or d.get('workspace') or ''
    p = urllib.parse.urlparse(uri)
    host = ''
    if p.scheme == 'vscode-remote':
        host = urllib.parse.unquote(p.netloc).split('+', 1)[-1] + ':'
    return host + urllib.parse.unquote(p.path)


def _md(v):
    if isinstance(v, dict):
        v = v.get('value', '')
    return _MDLINK.sub(lambda m: m.group(1) or m.group(2), v or '')


def _uri_path(u):
    if isinstance(u, dict):
        return u.get('fsPath') or u.get('path') or ''
    return str(u or '')


def parse(path, titles=None):
    state = replay(path) or {}
    sid = state.get('sessionId') or os.path.splitext(os.path.basename(path))[0]
    conv = Conversation('copilot', sid, path)
    conv.project = workspace_folder(path)
    conv.started = parse_ts(state.get('creationDate'))
    requests = [r for r in (state.get('requests') or []) if isinstance(r, dict)]
    for r in requests:
        dt = parse_ts(r.get('timestamp'))
        if dt:
            conv.started = conv.started or dt
            conv.updated = dt
        if not conv.tool_version:
            conv.tool_version = ((r.get('agent') or {}).get('extensionVersion') or '')
        user_text = (r.get('message') or {}).get('text') or ''
        conv.msg('user', dt, user_text)
        rdt = parse_ts(r.get('responseTimestamp')) or dt
        if rdt:
            conv.updated = rdt
        buf = []

        def flush():
            if buf:
                conv.msg('assistant', dt, ''.join(buf))
                buf.clear()
        for part in r.get('response') or []:
            if not isinstance(part, dict):
                continue
            k = part.get('kind')
            if k is None and 'value' in part:
                buf.append(part.get('value') or '')
            elif k == 'markdownContent':
                buf.append(_md(part.get('content')))
            elif k == 'inlineReference':
                ref = part.get('inlineReference') or {}
                name = part.get('name') or os.path.basename(_uri_path(ref.get('uri', ref)) or ref.get('name', ''))
                buf.append(f'`{name}`')
            elif k == 'toolInvocationSerialized':
                flush()
                tsd = part.get('toolSpecificData') or {}
                tid = part.get('toolId') or 'tool'
                if tsd.get('kind') == 'terminal':
                    cl = tsd.get('commandLine') or {}
                    cmd = cl.get('original') or cl.get('userEdited') or cl.get('toolEdited') or ''
                    item = conv.tool_call(dt, f'shell: {inline_cmd(cmd)}')
                    out = (tsd.get('terminalCommandOutput') or {}).get('text')
                    if out:
                        item[3] = _ANSI.sub('', out).replace('\r', '')
                    code = (tsd.get('terminalCommandState') or {}).get('exitCode')
                    if code not in (None, 0):
                        item[3] = f'(exit {code})\n' + (item[3] or '')
                elif tid == 'manage_todo_list' or part.get('presentation') == 'hidden':
                    continue
                else:
                    msg = _md(part.get('pastTenseMessage') or part.get('invocationMessage'))
                    conv.tool_call(dt, f'{tid}: {msg}'.strip())
            elif k == 'textEditGroup':
                flush()
                added = sum(len((e.get('text') or '').splitlines()) for grp in part.get('edits') or [] for e in (grp or []))
                conv.tool_call(dt, f'edit: {_uri_path(part.get("uri"))} (+{added})')
            elif k == 'questionCarousel':
                flush()
                qs = [q.get('title') or q.get('message') or '' for q in part.get('questions') or []]
                conv.tool_call(dt, 'ask_user: ' + '; '.join(q for q in qs if q))
        flush()
    conv.title = state.get('customTitle') or ''
    if not conv.title:
        first = conv.first_user_text().strip().splitlines()
        conv.title = first[0][:80] if first else 'untitled'
    if not requests or conv.user_turns == 0:
        conv.skip = 'empty'
    return conv
