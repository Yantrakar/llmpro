"""Codex CLI / VS Code / Desktop rollouts (~/.codex/sessions, ~/.codex/archived_sessions)."""
import glob
import json
import os
import re
import sqlite3

from .. import store
from ..redact import Conversation, config_paths, inline_cmd, parse_ts


def roots():
    # ~/.codex, then project-local CODEX_HOME folders from config.toml [sources] codex_extra (never read their auth.json).
    return [os.path.join(store.home(), '.codex')] + config_paths('sources', 'codex_extra')


_INJECTED_BLOCKS = re.compile(
    r'<(environment_context|user_instructions|permissions instructions|turn_aborted|collaboration_mode|'
    r'apps_instructions|skills_instructions|user_shell_command|INSTRUCTIONS|personality_spec)>.*?</\1>\s*', re.S)
_AGENTS_MD = re.compile(r'^# AGENTS\.md instructions for .*?(?:</INSTRUCTIONS>|\Z)\s*', re.S)
_IDE_REQUEST = re.compile(r'## My request for Codex:\s*\n', re.S)


def source_files():
    files = []
    for root in roots():
        files += glob.glob(os.path.join(root, 'sessions', '**', '*.jsonl'), recursive=True)
        files += glob.glob(os.path.join(root, 'archived_sessions', '*.jsonl'))
    return sorted(files)


def load_titles():
    titles = {}
    for root in roots():
        try:
            for line in open(os.path.join(root, 'session_index.jsonl'), encoding='utf-8'):
                e = json.loads(line)
                if e.get('thread_name'):
                    titles[e['id']] = e['thread_name']
        except (OSError, ValueError):
            pass
    for db in sorted(f for root in roots() for f in glob.glob(os.path.join(root, 'state_*.sqlite'))):
        try:
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            for tid, title, name in con.execute('select id, title, name from threads'):
                t = name or title
                if t and tid not in titles:
                    titles[tid] = t
            con.close()
        except sqlite3.Error:
            pass
    return titles


def clean_user_text(text):
    text = _INJECTED_BLOCKS.sub('', text)
    text = _AGENTS_MD.sub('', text)
    if text.lstrip().startswith('# Context from my IDE setup'):
        m = _IDE_REQUEST.search(text)
        text = text[m.end():] if m else ''
    return text.strip()


def _content_text(content):
    parts = []
    for c in content or []:
        if not isinstance(c, dict):
            continue
        if c.get('type') in ('input_text', 'output_text', 'text'):
            parts.append(c.get('text', ''))
        elif c.get('type') in ('input_image', 'image'):
            parts.append('[image]')
    return '\n'.join(p for p in parts if p)


def _patch_lines(patch):
    """apply_patch body -> one 'edit: path (+a -b)' line per file."""
    out, cur, plus, minus, op = [], None, 0, 0, 'edit'

    def flush():
        if cur:
            out.append(f'{op}: {cur} (+{plus} -{minus})')
    for line in (patch or '').splitlines():
        m = re.match(r'^\*\*\* (Update|Add|Delete) File: (.+)$', line)
        if m:
            flush()
            op = {'Update': 'edit', 'Add': 'write', 'Delete': 'delete'}[m.group(1)]
            cur, plus, minus = m.group(2).strip(), 0, 0
        elif line.startswith('*** Move to: '):
            cur = cur + ' -> ' + line[13:].strip()
        elif cur and line.startswith('+') and not line.startswith('+++'):
            plus += 1
        elif cur and line.startswith('-') and not line.startswith('---'):
            minus += 1
    flush()
    return out or ['edit: (patch)']


def _clean_output(out):
    if isinstance(out, dict):
        out = out.get('output', json.dumps(out))
    if isinstance(out, list):
        out = '\n'.join(x.get('text', '') for x in out if isinstance(x, dict))
    out = str(out or '')
    if out.startswith('{'):
        try:
            d = json.loads(out)
            if isinstance(d, dict) and 'output' in d:
                out = str(d['output'])
        except ValueError:
            pass
    if out.startswith('Chunk ID:') or out.startswith('Exit code:') or out.startswith('Wall time:'):
        m = re.search(r'^Output:\n', out, re.M)
        exit_m = re.search(r'(?:Process exited with code|Exit code:) (\d+)', out)
        body = out[m.end():] if m else ''
        if exit_m and exit_m.group(1) != '0':
            body = f'(exit {exit_m.group(1)})\n' + body
        out = body
    return out


def _describe_call(name, args):
    """-> (list of summary lines, is_edit)"""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {'input': args}
    args = args or {}
    if name == 'apply_patch':
        return _patch_lines(args.get('input') or args.get('patch') or ''), True
    if name in ('exec_command', 'shell', 'shell_command', 'local_shell', 'container.exec'):
        cmd = args.get('cmd') or args.get('command') or ''
        if isinstance(cmd, list):
            cmd = cmd[2] if len(cmd) == 3 and cmd[1] in ('-lc', '-c') else ' '.join(cmd)
        return [f'shell: {inline_cmd(cmd)}'], False
    if name == 'update_plan':
        return ['update_plan'], False
    if name in ('exec', 'js', 'python'):
        code = args.get('input') or args.get('code') or ''
        return [f'{name}: {inline_cmd(code)}'], False
    if name in ('view_image',):
        return [f'view_image: {args.get("path", "")}'], False
    brief = json.dumps(args, ensure_ascii=False)
    if len(brief) > 160:
        brief = brief[:160] + ' …'
    return [f'{name}: {brief}'], False


def parse(path, titles):
    events = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    meta = next((e['payload'] for e in events if e.get('type') == 'session_meta'), {}) or {}
    sid = meta.get('id') or os.path.basename(path)[-41:-5]
    conv = Conversation('codex', sid, path)
    conv.tool_version = meta.get('cli_version', '')
    conv.project = meta.get('cwd', '')
    conv.started = parse_ts(meta.get('timestamp')) or parse_ts(events[0].get('timestamp') if events else None)

    src = meta.get('source')
    if isinstance(src, dict) and 'subagent' in src:
        conv.skip = 'subagent'

    has_items = any(e.get('type') == 'response_item' and (e.get('payload') or {}).get('type') == 'message'
                    and (e.get('payload') or {}).get('role') in ('user', 'assistant') for e in events)
    calls = {}
    last_ts = None
    for e in events:
        p = e.get('payload') or {}
        t, pt = e.get('type'), p.get('type')
        dt = parse_ts(e.get('timestamp'))
        if dt:
            last_ts = dt
        if t == 'response_item' and pt == 'message' and has_items:
            role = p.get('role')
            text = _content_text(p.get('content'))
            if role == 'user':
                text = clean_user_text(text)
                if text and conv.user_turns == 0 and text.startswith('The following is the Codex agent history'):
                    conv.skip = 'subagent'
                conv.msg('user', dt, text)
            elif role == 'assistant':
                conv.msg('assistant', dt, text)
        elif t == 'event_msg' and not has_items and pt in ('user_message', 'agent_message'):
            if pt == 'user_message':
                conv.msg('user', dt, clean_user_text(p.get('message', '')))
            else:
                conv.msg('assistant', dt, p.get('message', ''))
        elif t == 'response_item' and pt in ('function_call', 'custom_tool_call'):
            args = p.get('arguments') if pt == 'function_call' else p.get('input')
            if pt == 'custom_tool_call' and p.get('name') == 'apply_patch':
                args = {'input': p.get('input')}
            lines, is_edit = _describe_call(p.get('name', ''), args)
            items = [conv.tool_call(dt, ln) for ln in lines]
            if not is_edit:
                calls[p.get('call_id')] = items[-1]
        elif t == 'response_item' and pt == 'local_shell_call':
            lines, _ = _describe_call('local_shell', (p.get('action') or {}))
            calls[p.get('call_id')] = conv.tool_call(dt, lines[0])
        elif t == 'response_item' and pt == 'web_search_call':
            q = (p.get('action') or {}).get('query') or ''
            conv.tool_call(dt, f'web_search: {q}' if q else 'web_search')
        elif t == 'response_item' and pt in ('function_call_output', 'custom_tool_call_output', 'local_shell_call_output'):
            item = calls.get(p.get('call_id'))
            if item is not None:
                item[3] = _clean_output(p.get('output'))
    conv.updated = last_ts
    title = titles.get(sid)
    if not title:
        first = conv.first_user_text().strip().splitlines()
        title = first[0][:80] if first else 'untitled'
    conv.title = title
    if not conv.skip and conv.user_turns == 0:
        conv.skip = 'empty'
    return conv
