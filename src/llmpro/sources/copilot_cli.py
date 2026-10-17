"""GitHub Copilot CLI sessions (~/.copilot/session-state/<id>/events.jsonl, one event per line).

Kept: user.message (the typed text, not transformedContent with its injected context), assistant.message
content, and each tool call as one line with its output. Dropped: system.message, model.* bookkeeping,
reasoning, usage checkpoints.
"""
import json
import os

from .. import store
from ..redact import Conversation, inline_cmd, parse_ts


def root():
    return os.path.join(store.home(), '.copilot', 'session-state')


def source_files():
    base = root()
    if not os.path.isdir(base):
        return []
    return sorted(p for p in (os.path.join(base, d, 'events.jsonl') for d in os.listdir(base)) if os.path.isfile(p))


def _workspace(folder):
    """workspace.yaml is flat key: value lines; read the few keys needed without a YAML library."""
    out = {}
    try:
        for line in open(os.path.join(folder, 'workspace.yaml'), encoding='utf-8'):
            key, sep, value = line.partition(':')
            if sep and key.strip() in ('cwd', 'name', 'user_named', 'created_at', 'updated_at'):
                out[key.strip()] = value.strip().strip('"\'')
    except OSError:
        pass
    return out


def _describe(name, args):
    args = args or {}
    if name in ('bash', 'shell', 'powershell'):
        return f'shell: {inline_cmd(args.get("command", ""))}', False
    if name in ('edit', 'str_replace', 'str_replace_editor', 'create', 'write'):
        path = args.get('path') or args.get('filePath') or ''
        new = args.get('new_str') or args.get('file_text') or args.get('content') or ''
        old = args.get('old_str') or ''
        return f'{"write" if name in ("create", "write") else "edit"}: {path} (+{len(new.splitlines())} -{len(old.splitlines())})', True
    if name == 'view':
        return f'read: {args.get("path", "")}', False
    brief = json.dumps(args, ensure_ascii=False)
    if len(brief) > 160:
        brief = brief[:160] + ' …'
    return f'{name}: {brief}', False


def parse(source, titles=None):
    folder = os.path.dirname(source)
    ws = _workspace(folder)
    conv = Conversation('copilot-cli', os.path.basename(folder), source)
    conv.project = ws.get('cwd', '')
    conv.title = ws.get('name', '')
    conv.started, conv.updated = parse_ts(ws.get('created_at')), parse_ts(ws.get('updated_at'))
    calls = {}  # toolCallId -> (dt, summary, is_edit), until the matching tool.execution_complete
    for line in open(source, encoding='utf-8'):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t, d, dt = e.get('type'), e.get('data') or {}, parse_ts(e.get('timestamp'))
        if t == 'session.start':
            conv.tool_version = d.get('copilotVersion', '')
            conv.project = conv.project or (d.get('context') or {}).get('cwd', '')
            conv.started = conv.started or parse_ts(d.get('startTime'))
        elif t == 'user.message' and d.get('content'):
            conv.msg('user', dt, d['content'])
        elif t == 'assistant.message' and d.get('content'):
            conv.msg('assistant', dt, d['content'])
        elif t == 'tool.execution_start':
            summ, is_edit = _describe(d.get('toolName', ''), d.get('arguments'))
            calls[d.get('toolCallId')] = (dt, summ, is_edit)
        elif t == 'tool.execution_complete':
            dt0, summ, is_edit = calls.pop(d.get('toolCallId'), (dt, 'tool', False))
            result = d.get('result') or {}
            out = result.get('content') if d.get('success') else f'(error) {result.get("content") or d.get("error", "")}'
            conv.tool_call(dt0, summ, None if is_edit else out)
        if dt:
            conv.updated = max(conv.updated, dt) if conv.updated else dt
    for dt, summ, _ in calls.values():  # started but never finished (session aborted)
        conv.tool_call(dt, summ, '(no result: session ended)')
    if conv.user_turns == 0:
        conv.skip = 'empty'
    return conv
