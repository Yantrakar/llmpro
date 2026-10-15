"""Claude Code sessions (~/.claude/projects/<cwd-slug>/<session-id>.jsonl)."""
import glob
import json
import os
import re

from .. import store
from ..redact import Conversation, config_paths, inline_cmd, parse_ts


def default_root():
    return os.path.join(store.home(), '.claude')


def extra_roots():
    # Extra Claude Code data folders (CLAUDE_CONFIG_DIR mounted into containers), from config.toml [sources] claude_extra.
    return config_paths('sources', 'claude_extra')


_DROP_BLOCKS = re.compile(
    r'<(system-reminder|local-command-caveat|local-command-stdout|local-command-stderr|ide_selection|ide_opened_file|'
    r'ide_diagnostics|task-notification|user-prompt-submit-hook|session-start-hook|command-message|'
    r'new-diagnostics|bash-stderr)>.*?</\1>\s*', re.S)
_COMMAND = re.compile(r'<command-name>(.*?)</command-name>\s*(?:<command-args>(.*?)</command-args>)?', re.S)
_BASH_IN = re.compile(r'<bash-input>(.*?)</bash-input>', re.S)
_BASH_OUT = re.compile(r'<bash-stdout>(.*?)</bash-stdout>', re.S)
_INTERRUPT = re.compile(r'^\[Request interrupted by user[^\]]*\]\s*$', re.M)


def source_files():
    files = []
    for root in [default_root()] + extra_roots():
        files += glob.glob(os.path.join(root, 'projects', '*', '*.jsonl'))
    return sorted(files)


def clean_user_text(text):
    text = _DROP_BLOCKS.sub('', text)
    text = _COMMAND.sub(lambda m: (m.group(1).strip() + ' ' + (m.group(2) or '').strip()).strip(), text)
    text = _INTERRUPT.sub('', text)
    return text.strip()


def _result_text(content):
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get('type') == 'text':
                parts.append(b.get('text', ''))
            elif b.get('type') == 'image':
                parts.append('[image]')
    return '\n'.join(parts)


def _nlines(s):
    return len((s or '').splitlines())


def _describe(name, inp):
    """-> (summary, is_edit)"""
    inp = inp or {}
    if name == 'Bash':
        return f'shell: {inline_cmd(inp.get("command", ""))}', False
    if name == 'Read':
        return f'read: {inp.get("file_path", "")}', False
    if name == 'Edit':
        return f'edit: {inp.get("file_path", "")} (+{_nlines(inp.get("new_string"))} -{_nlines(inp.get("old_string"))})', True
    if name == 'MultiEdit':
        edits = inp.get('edits') or []
        a = sum(_nlines(e.get('new_string')) for e in edits)
        r = sum(_nlines(e.get('old_string')) for e in edits)
        return f'edit: {inp.get("file_path", "")} (+{a} -{r})', True
    if name == 'Write':
        return f'write: {inp.get("file_path", "")} (+{_nlines(inp.get("content"))})', True
    if name == 'NotebookEdit':
        return f'edit: {inp.get("notebook_path", "")} (notebook cell)', True
    if name in ('Grep', 'Glob'):
        where = inp.get('path') or ''
        return f'{name.lower()}: {inline_cmd(inp.get("pattern", ""))}' + (f' in {where}' if where else ''), False
    if name in ('Agent', 'Task'):
        return f'agent: {inp.get("description") or inp.get("subagent_type") or ""}', False
    if name == 'WebFetch':
        return f'web_fetch: {inp.get("url", "")}', False
    if name == 'WebSearch':
        return f'web_search: {inp.get("query", "")}', False
    if name in ('TodoWrite', 'TaskCreate', 'TaskUpdate'):
        return name, False
    brief = json.dumps(inp, ensure_ascii=False)
    if len(brief) > 160:
        brief = brief[:160] + ' …'
    return f'{name}: {brief}', False


def parse(path, titles=None):
    sid = os.path.basename(path)[:-6]
    conv = Conversation('claude-code', sid, path)
    seen = set()
    calls = {}
    custom_title = ai_title = summary_title = None
    any_main = any_side = False
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get('type')
            if t == 'custom-title':
                custom_title = e.get('customTitle') or custom_title
            elif t == 'ai-title':
                ai_title = e.get('aiTitle') or ai_title
            elif t == 'summary':
                summary_title = summary_title or e.get('summary')
            if t not in ('user', 'assistant'):
                continue
            if e.get('isSidechain'):
                any_side = True
                continue
            any_main = True
            uid = e.get('uuid')
            if uid in seen:
                continue
            seen.add(uid)
            if e.get('isMeta') or e.get('isCompactSummary') or e.get('isVisibleInTranscriptOnly'):
                continue
            dt = parse_ts(e.get('timestamp'))
            if dt:
                conv.started = conv.started or dt
                conv.updated = dt
            conv.project = conv.project or e.get('cwd', '')
            conv.tool_version = e.get('version') or conv.tool_version
            msg = e.get('message') or {}
            content = msg.get('content')
            if t == 'user':
                if isinstance(content, str):
                    blocks = [{'type': 'text', 'text': content}]
                else:
                    blocks = content or []
                texts = []
                for b in blocks:
                    bt = b.get('type')
                    if bt == 'text':
                        raw = b.get('text', '')
                        bash_in = _BASH_IN.search(raw)
                        bash_out = _BASH_OUT.search(raw)
                        if bash_in:
                            conv.tool_call(dt, f'shell (user): {inline_cmd(bash_in.group(1))}')
                            continue
                        if bash_out and conv.items and conv.items[-1][0] == 'tool':
                            conv.items[-1][3] = bash_out.group(1)
                            continue
                        texts.append(clean_user_text(raw))
                    elif bt == 'image':
                        texts.append('[image]')
                    elif bt == 'document':
                        texts.append('[document]')
                    elif bt == 'tool_result':
                        item = calls.get(b.get('tool_use_id'))
                        if item is not None:
                            item[3] = _result_text(b.get('content'))
                conv.msg('user', dt, '\n\n'.join(x for x in texts if x))
            else:
                for b in content if isinstance(content, list) else [{'type': 'text', 'text': content or ''}]:
                    bt = b.get('type')
                    if bt == 'text':
                        conv.msg('assistant', dt, b.get('text', ''))
                    elif bt == 'tool_use':
                        summ, is_edit = _describe(b.get('name', ''), b.get('input'))
                        item = conv.tool_call(dt, summ)
                        if not is_edit:
                            calls[b.get('id')] = item
    if any_side and not any_main:
        conv.skip = 'subagent'
    conv.title = custom_title or ai_title or summary_title or ''
    if not conv.title:
        first = conv.first_user_text().strip().splitlines()
        conv.title = first[0][:80] if first else 'untitled'
    if not conv.skip and conv.user_turns == 0:
        conv.skip = 'empty'
    return conv
