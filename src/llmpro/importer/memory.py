"""Map tool-native memories into STORE/memory/<name>.md and rebuild STORE/memory/INDEX.md.

Sources: Claude Code  ~/.claude/projects/<slug>/memory/*.md
         Copilot      ~/.config/Code/User/workspaceStorage/<hash>/GitHub.copilot-chat/memory-tool/memories/**/*.md
         Codex        ~/.codex/memories/ and memories_*.sqlite stage1_outputs are empty on this machine, so no reader yet

Renames and scopes chosen by hand live in the store's .agentspro/memory_overrides.json (or the older
tools/memory_overrides.json while the store still has it):
  {"rename": {"<default name>": "<name>"}, "scope": {"<tool>:<original id>": "<scope>"}, "type": {...}}

A memory already in the store keeps any fields edited there (scope, status, superseded_by, cue, evidence,
confidence, body)
unless the source changed since it was last imported. Even then, anything below the STORE_MARK line
(facts merged in from conversations) is kept. Hand-written memories (no tool source) are never touched.
"""
import datetime
import glob
import json
import os
import re
import sys
import urllib.parse

from ..redact import (MACHINE, frontmatter, is_local_project, load_manifest, manifest_entry,
                      read_frontmatter, redact, save_manifest, write_text)
from ..store import LOCAL_ONLY, data_file, home

TYPES = ('user', 'feedback', 'project', 'reference')

OVERRIDES_FILE = 'memory_overrides.json'
STORE_MARK = '## Added in the store'


def claude_projects():
    return os.path.join(home(), '.claude', 'projects')


def vscode_user_dirs():
    return [os.path.join(home(), '.config', d, 'User') for d in ('Code', 'Code - Insiders')]


def memory_name(stem):
    stem = re.sub(r'^(feedback|project|user|reference)[_-]', '', stem)
    return re.sub(r'[^a-z0-9]+', '-', stem.lower()).strip('-')


def session_cwd(slug):
    """The folder Claude Code ran in for a project slug, from its sessions; '' if none is left."""
    cwd = ''
    for session in glob.glob(os.path.join(claude_projects(), slug, '*.jsonl')):
        for line in open(session, encoding='utf-8'):
            m = re.search(r'"cwd":"([^"]+)"', line)
            if m:
                cwd = m.group(1)
                break
        if cwd:
            break
    return cwd


def claude_local_only(slug):
    """A project slug under a local-only folder ([privacy] in config.toml): by its sessions' folder, or by
    the slug itself, which is the folder with every other character turned into '-'."""
    if is_local_project(session_cwd(slug)):
        return True
    return any(slug == s or slug.startswith(s + '-') for s in (re.sub(r'[^A-Za-z0-9]', '-', d) for d in LOCAL_ONLY))


def default_scope(slug):
    """Scope = the project folder the memory was written in; the home folder means global."""
    home_slug = re.sub(r'[^A-Za-z0-9]', '-', home())
    if slug == home_slug:  # the Claude project slug of the home folder itself
        return 'global'
    cwd = session_cwd(slug)
    if not cwd:  # no sessions left: decode the slug, dropping the home prefix and a folder directly under home
        cwd = slug[len(home_slug):] if slug.startswith(home_slug) else slug
        first, _, rest = cwd.strip('-').partition('-')
        cwd = rest if rest and first and os.path.isdir(os.path.join(home(), first)) else cwd
    if os.path.normpath(cwd) == home():
        return 'global'
    return re.sub(r'[^a-z0-9.]+', '-', os.path.basename(cwd.rstrip('/')).lower()).strip('-') or 'global'


def _date(value):
    if not value:
        return ''
    return str(value)[:10]


def claude_sources():
    for path in sorted(glob.glob(os.path.join(claude_projects(), '*', 'memory', '*.md'))):
        if os.path.basename(path) == 'MEMORY.md':
            continue
        slug = path.split(os.sep)[-3]
        stem = os.path.splitext(os.path.basename(path))[0]
        yield slug, stem, path


def copilot_sources():
    """-> (scope, original id, path, local-only) for each Copilot memory-tool file."""
    for user_dir in vscode_user_dirs():
        for ws in sorted(glob.glob(os.path.join(user_dir, 'workspaceStorage', '*'))):
            root = os.path.join(ws, 'GitHub.copilot-chat', 'memory-tool', 'memories')
            files = sorted(glob.glob(os.path.join(root, '**', '*.md'), recursive=True))
            if not files:
                continue
            scope, folder = 'global', ''
            try:
                folder = json.load(open(os.path.join(ws, 'workspace.json'), encoding='utf-8')).get('folder', '')
                scope = re.sub(r'[^a-z0-9.]+', '-', folder.rstrip('/').rsplit('/', 1)[-1].lower()).strip('-') or 'global'
            except (OSError, ValueError):
                pass
            local = is_local_project(urllib.parse.unquote(folder[len('file://'):]) if folder.startswith('file://') else '')
            for path in files:
                yield scope, os.path.basename(ws) + '/' + os.path.relpath(path, root)[:-3], path, local


def convert_copilot(scope, oid, path, name, overrides):
    """Copilot memories are plain markdown: the first heading is the description, the rest is the fact."""
    text = open(path, encoding='utf-8').read()
    m = re.match(r'\s*#+\s*(.+)\n', text)
    desc = m.group(1).strip() if m else os.path.basename(path)[:-3]
    body = text[m.end():] if m else text
    day = datetime.datetime.fromtimestamp(os.path.getmtime(path)).astimezone().strftime('%Y-%m-%d')
    key = f'copilot:{oid}'
    return {
        'name': name,
        'description': redact(desc),
        'type': overrides.get('type', {}).get(key, 'project'),
        'scope': overrides.get('scope', {}).get(key, scope),
        'status': 'active',
        'sources': [key],
        'created': day,
        'updated': day,
    }, redact(body.strip()) + '\n'


def lesson_fields(fields):
    """cue, evidence and confidence of a source memory. A local/ or sessions/ entry would name a file that
    never leaves the machine, so it is dropped."""
    out = {}
    if fields.get('cue'):
        out['cue'] = redact(fields['cue'])
    ev = fields.get('evidence') or []
    ev = [e for e in (ev if isinstance(ev, list) else [ev]) if not e.startswith(('local/', 'sessions/'))]
    if ev:
        out['evidence'] = ev
    if fields.get('confidence'):
        out['confidence'] = fields['confidence']
    return out


def convert_claude(slug, stem, path, name_map, overrides):
    fields, body = read_frontmatter(path)
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
    mtype = fields.get('type') or ''
    if mtype not in TYPES:
        mtype = 'reference'
    updated = _date(fields.get('modified')) or mtime.strftime('%Y-%m-%d')
    created = _date(fields.get('created')) or updated
    key = f'{slug}/{stem}'

    def relink(m):
        target = m.group(1).strip()
        return f'[[{name_map.get(target) or overrides.get("rename", {}).get(memory_name(target), memory_name(target))}]]'
    body = re.sub(r'\[\[([^\]]+)\]\]', relink, body)
    desc = re.sub(r'\[\[([^\]]+)\]\]', relink, fields.get('description', ''))
    return {
        **lesson_fields(fields),
        'name': name_map[stem],
        'description': redact(desc),
        'type': mtype,
        'scope': overrides.get('scope', {}).get(f'claude-code:{key}', default_scope(slug)),
        'status': 'active',
        'sources': [f'claude-code:{key}'],
        'created': created,
        'updated': updated,
    }, redact(body.strip()) + '\n'


def write_memory(store, fields, body):
    """store: the store, or its local/ folder for a local-only project's memory."""
    path = os.path.join(store, 'memory', fields['name'] + '.md')
    order = ['name', 'description', 'type', 'scope', 'status', 'superseded_by', 'sources', 'cue', 'evidence', 'confidence',
             'created', 'updated']
    fm = {k: fields[k] for k in order if fields.get(k) not in (None, '', [])}
    text = frontmatter(fm) + '\n' + body
    if not os.path.exists(path) or open(path, encoding='utf-8').read() != text:
        write_text(path, text)
    return path


def index_text(store):
    """The text memory/INDEX.md should have."""
    rows = []
    for path in sorted(glob.glob(os.path.join(store, 'memory', '*.md'))):
        if os.path.basename(path) == 'INDEX.md':
            continue
        f, body = read_frontmatter(path)
        if f.get('status') == 'superseded':
            continue
        title = f.get('title') or f.get('name', '').replace('-', ' ').capitalize()
        scope = f.get('scope', 'global')
        rows.append((scope != 'global', scope, f.get('type', ''), title, os.path.basename(path), f.get('description', '')))
    out = ['# Memory index', '',
           'One line per active memory. Superseded memories are kept as files but left out of this list.', '']
    current = None
    for _, scope, mtype, title, fname, desc in sorted(rows):
        if scope != current:
            out += ['', f'## {scope}', ''] if current is not None else [f'## {scope}', '']
            current = scope
        out.append(f'- [{title}]({fname}) — {desc}')
    return '\n'.join(out) + '\n'


def rebuild_index(store):
    write_text(os.path.join(store, 'memory', 'INDEX.md'), index_text(store))


def run(store, only=None, index_only=False):
    if index_only:
        rebuild_index(store)
        return
    overrides = {}
    ov_path = data_file(store, OVERRIDES_FILE, 'memory_overrides.json')
    if os.path.exists(ov_path):
        overrides = json.load(open(ov_path, encoding='utf-8'))
    manifest = load_manifest(store) if not only else {}

    renames = overrides.get('rename', {})
    local_slugs = {}
    sources = []
    for slug, stem, path in claude_sources():
        if slug not in local_slugs:
            local_slugs[slug] = claude_local_only(slug)
        sources.append(('claude-code', f'{slug}/{stem}', path, (slug, stem), local_slugs[slug]))
    sources += [('copilot', oid, path, scope, local) for scope, oid, path, local in copilot_sources()]
    name_map = {}
    for tool, oid, path, _, _ in sources:
        stem = os.path.basename(oid)
        default = memory_name(stem)
        name_map[stem] = renames.get(default, default)
    seen = {}
    for tool, oid, path, extra, local in sources:
        stem = os.path.basename(oid)
        name = name_map[stem]
        if only and name not in only:
            continue
        key = (tool, MACHINE, 'memory', oid)
        if name in seen and seen[name] != f'{tool}:{oid}':
            manifest[key] = manifest_entry(tool, 'memory', path, oid, '', f'skipped:name-clash-with:{seen[name]}')
            print(f'name clash: {name} ({tool}:{oid} vs {seen[name]})', file=sys.stderr)
            continue
        seen[name] = f'{tool}:{oid}'
        prev = manifest.get(key) or {}
        # a local-only project's memory goes to local/memory/, which git ignores (config.toml [privacy])
        base = os.path.join(store, 'local') if local else store
        out = os.path.join(base, 'memory', name + '.md')
        src_mtime = manifest_entry(tool, 'memory', path, '', '', '')['mtime']
        if os.path.exists(out) and prev.get('mtime') == src_mtime:
            # unchanged at the source: keep whatever was curated in the store
            manifest[key] = prev
            continue
        if tool == 'claude-code':
            fields, body = convert_claude(extra[0], extra[1], path, name_map, overrides)
        else:
            fields, body = convert_copilot(extra, oid, path, name, overrides)
        if os.path.exists(out):
            old, _ = read_frontmatter(out)
            for k in ('scope', 'status', 'superseded_by', 'created', 'cue', 'evidence', 'confidence'):
                if old.get(k):
                    fields[k] = old[k]
            srcs = old.get('sources') or []
            fields['sources'] = sorted(set(srcs if isinstance(srcs, list) else [srcs]) | set(fields['sources']))
            old_text = open(out, encoding='utf-8').read()
            if STORE_MARK in old_text:
                body = body.rstrip('\n') + '\n\n' + old_text[old_text.index(STORE_MARK):]
                fields['updated'] = max(fields['updated'], old.get('updated') or '')
        write_memory(base, fields, body)
        manifest[key] = manifest_entry(tool, 'memory', path, oid, os.path.relpath(out, store), 'imported')
        if only:
            print(out)

    if not only:
        save_manifest(store, manifest)
        rebuild_index(store)
