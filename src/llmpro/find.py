"""`llmpro find`: search the conversations and memories of the store. Plain reading, no index.

Reads history/ and memory/ (and local/history/, which exists on this machine only; --shared-only leaves it out).
Never reads sessions/.
Case-insensitive; the words of the query must all occur in the same file.
"""
import os
import re
import sys

from . import jsonout
from .redact import parse_frontmatter

LINE_LIMIT = 160
MAX_LINES = 3
DATE = re.compile(r'(\d{4}-\d\d-\d\d)')


def walk(root):
    """Every .md file under root."""
    stack = [root]
    while stack:
        try:
            it = os.scandir(stack.pop())
        except OSError:
            continue
        with it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                elif e.name.endswith('.md') and e.is_file(follow_symlinks=False):
                    yield e.path


def candidates(store, machine, tool, since, want_history, want_memory, shared_only=False):
    """-> [(kind, path, rel, date, machine, tool)] of the files worth reading, filtered by what the path says."""
    out = []
    if want_history:
        bases = ('history',) if shared_only else ('history', os.path.join('local', 'history'))
        for base in bases:
            for path in walk(os.path.join(store, base)):
                rel = os.path.relpath(path, store)
                parts = rel.split(os.sep)
                n = parts.index('history')
                if len(parts) < n + 4:
                    continue
                m, t = parts[n + 1], parts[n + 2]
                d = DATE.match(parts[-1])
                date = d.group(1) if d else ''
                if machine and m != machine or tool and t != tool or since and date < since:
                    continue
                out.append(('history', path, rel, date, m, t))
    if want_memory and not tool:
        for path in walk(os.path.join(store, 'memory')):
            if os.path.basename(path) == 'INDEX.md':
                continue
            out.append(('memory', path, os.path.relpath(path, store), '', '', ''))
    return out


def matching_lines(text, words):
    found = []
    for line in text.splitlines():
        low = line.lower()
        if any(w in low for w in words):
            found.append(line.strip()[:LINE_LIMIT])
            if len(found) == MAX_LINES:
                break
    return found


def first_line(value):
    for line in (value or '').splitlines():
        if line.strip():
            return line.strip()
    return ''


def search(store, words, machine=None, tool=None, since=None, history=True, memory=True, verbose=False,
           shared_only=False):
    """-> [hit dict], newest first. A hit: kind, date, machine, tool, scope, title, path, summary, lines."""
    words = [w.lower() for w in words]
    hits = []
    for kind, path, rel, date, m, t in candidates(store, machine, tool, since, history, memory, shared_only):
        try:
            with open(path, encoding='utf-8', errors='replace') as fh:
                text = fh.read()
        except OSError:
            continue
        low = text.lower()
        if not all(w in low for w in words):
            continue
        fields, _ = parse_frontmatter(text)
        if kind == 'memory':
            scope = fields.get('scope') or 'global'
            date = str(fields.get('updated') or fields.get('created') or '')
            if machine and scope != machine or since and date < since:
                continue
            hit = {'kind': 'memory', 'date': date, 'machine': '', 'tool': '', 'scope': scope,
                   'title': fields.get('title') or fields.get('name') or os.path.basename(path)[:-3],
                   'path': rel, 'summary': str(fields.get('description') or '')}
        else:
            hit = {'kind': 'history', 'date': date or str(fields.get('started') or '')[:10], 'machine': m,
                   'tool': t, 'scope': '', 'title': str(fields.get('title') or ''), 'path': rel,
                   'summary': first_line(fields.get('summary'))}
        if verbose:
            hit['lines'] = matching_lines(text, words)
        hits.append(hit)
    hits.sort(key=lambda h: (h['date'], h['path']), reverse=True)
    return hits


def one_line(s):
    return ' '.join(str(s).split())


def render(hits, verbose=False):
    out = []
    for h in hits:
        who = f"{h['machine']}/{h['tool']}" if h['kind'] == 'history' else f"memory/{h['scope']}"
        line = f"{h['date'] or '-':10}  {who}  {one_line(h['title'])}  {h['path']}"
        if h['summary']:
            line += '  - ' + one_line(h['summary'])
        out.append(line)
        for text in h.get('lines', []):
            out.append('    ' + one_line(text))
    return out


def run(store, query, machine=None, tool=None, since=None, history=True, memory=True, as_json=False, verbose=False,
        shared_only=False):
    """-> 0 if anything was found, 1 if not."""
    hits = search(store, query, machine, tool, since, history, memory, verbose, shared_only)
    if as_json:
        jsonout.emit('find', hits)
    else:
        for line in render(hits, verbose):
            print(line)
        if not hits:
            print('nothing found', file=sys.stderr)
    return 0 if hits else 1
