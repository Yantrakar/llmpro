"""Store health: the checks of `llmpro check` that are not about secrets (spec 3.4).

A stale memory/INDEX.md, a memory without the frontmatter FORMAT.md asks for, a history file that
manifest.jsonl does not list. Only the index can be fixed by the tool.
"""
import glob
import os
import re

from .importer import memory
from .redact import load_manifest, read_frontmatter, write_text

REQUIRED = ('name', 'description', 'type', 'scope')
TYPES = ('user', 'feedback', 'project', 'reference')
CONFIDENCE = ('high', 'medium', 'low')
SOURCE_ID = re.compile(r'^[a-z][a-z0-9-]*:\S+$')
SHOW = 5  # files named per kind of problem


def fix_index(store):
    """Rebuild memory/INDEX.md if it differs from what it should be. -> True if it was written."""
    if not os.path.isdir(os.path.join(store, 'memory')):
        return False
    path = os.path.join(store, 'memory', 'INDEX.md')
    want = memory.index_text(store)
    have = open(path, encoding='utf-8').read() if os.path.exists(path) else None
    if have == want:
        return False
    write_text(path, want)
    return True


def history_files(store):
    for base in ('history', os.path.join('local', 'history')):
        for d, _, names in os.walk(os.path.join(store, base)):
            for n in names:
                if n.endswith('.md'):
                    yield os.path.relpath(os.path.join(d, n), store)


def named(rels):
    rels = sorted(rels)
    return ', '.join(rels[:SHOW]) + (f' and {len(rels) - SHOW} more' if len(rels) > SHOW else '')


def evidence_faults(store, f):
    """-> what is wrong with a memory's evidence and confidence, as short phrases; [] if nothing."""
    out = []
    ev = f.get('evidence')
    if ev not in (None, '', []):
        for e in ev if isinstance(ev, list) else [ev]:
            if e.startswith(('local/', 'sessions/')) or e.startswith('/') or '..' in e:
                out.append(f'{e} may not be cited')
            elif e.startswith('history/'):
                if not os.path.isfile(os.path.join(store, e)):
                    out.append(f'{e} does not exist')
            elif not SOURCE_ID.match(e):
                out.append(f'{e} is not a history path or a source id')
    if f.get('confidence') not in (None, '') + CONFIDENCE:
        out.append(f'confidence {f["confidence"]} is not high, medium or low')
    return out


def problems(store):
    """-> [one line per kind of problem], empty when the store is healthy. Reads only."""
    out, wrong = [], {}
    mem = os.path.join(store, 'memory')
    if os.path.isdir(mem):
        path = os.path.join(mem, 'INDEX.md')
        have = open(path, encoding='utf-8').read() if os.path.exists(path) else None
        if have != memory.index_text(store):
            out.append('memory/INDEX.md is out of date' if have is not None else 'memory/INDEX.md is missing')
        bad = []
        for p in sorted(glob.glob(os.path.join(mem, '*.md'))):
            if os.path.basename(p) == 'INDEX.md':
                continue
            f, _ = read_frontmatter(p)
            if any(not f.get(k) for k in REQUIRED) or f.get('type') not in TYPES:
                bad.append(os.path.relpath(p, store))
            why = evidence_faults(store, f)
            if why:
                wrong[os.path.relpath(p, store)] = why
        if bad:
            out.append(f'{len(bad)} memories lack required frontmatter ({", ".join(REQUIRED)}; type one of '
                       f'{"|".join(TYPES)}): {named(bad)}')
    if wrong:
        what = '; '.join(sorted({w for v in wrong.values() for w in v}))
        out.append(f'{len(wrong)} memories cite evidence that is wrong ({what}): {named(wrong)}')
    listed = {e['output'] for e in load_manifest(store).values() if e.get('output')}
    missing = [r for r in history_files(store) if r not in listed]
    if missing:
        out.append(f'{len(missing)} history files are not in manifest.jsonl: {named(missing)}')
    return out
