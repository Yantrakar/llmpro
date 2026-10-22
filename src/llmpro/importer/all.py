"""Run every importer in one process, then sweep the whole store.

A secret the redactor finds in one source is learned (in memory only, never written down) and then
removed from every file in the store: whole copies, and pieces of 8 or more characters. That needs one
process, because a value found in a memory file may also sit in a conversation, and the other way round.

Then run `llmpro check` before committing (the pre-commit hook does).
"""
import hashlib
import json
import os
import re
import sys

from .. import REDACT_VERSION, __version__
from .. import redact as common
from ..redact import redact, step, sweep, write_text
from . import history as import_history
from . import instructions as import_instructions
from . import memory as import_memory

# Not skills/: each skill there is the only copy, written by hand, so the sweep must not edit it
# (2026-10-15: a bad learned value cut lines out of five skills). A secret in a skill is caught by
# secretcheck.py before it is committed and is fixed by hand.
SWEPT = ('history', 'memory', 'instructions')


def store_texts(store):
    for top in SWEPT:
        for d, dirs, files in os.walk(os.path.join(store, top)):
            for f in files:
                if re.search(r'\.tmp\d+$', f):
                    continue  # a write_text() temp file left by a killed run
                path = os.path.join(d, f)
                try:
                    yield path, open(path, encoding='utf-8').read()
                except (UnicodeDecodeError, OSError):
                    pass  # binary file


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]


# Files that redact() left unchanged in an earlier run, by content hash, for the redaction code then.
# redact() learns a value only where it replaces one, so an unchanged file teaches nothing and need not be
# redacted again; the sweep still runs over every file. Kept in .git, never committed: hashes only.
NOOP_CACHE = os.path.join('.git', 'agentspro', 'noop-redact.json')


def code_key():
    """-> the key of the no-op cache: REDACT_VERSION and a hash of redact.py's redaction section, the only code
    redact()'s result depends on (spec 23). So a release keeps the cache unless that code changed. Without
    the source, the version of the tool, as before 0.1.2."""
    try:
        text = open(common.__file__, encoding='utf-8').read()
        a = re.search(r'^# -+ redaction$', text, re.M)
        b = re.search(r'^# -+ text helpers$', text, re.M)
        if not (a and b and a.start() < b.start()):
            raise ValueError
        h = hashlib.sha256(text[a.start():b.start()].encode('utf-8')).hexdigest()[:16]
    except (OSError, ValueError, TypeError):
        h = __version__
    return f'redact{REDACT_VERSION}+{h}'


CODE = code_key()


def load_noop(store):
    try:
        cache = json.load(open(os.path.join(store, NOOP_CACHE), encoding='utf-8'))
        return set(cache['hashes']) if cache.get('code') == CODE else set()
    except (OSError, ValueError, KeyError):
        return set()


def save_noop(store, hashes):
    if os.path.isdir(os.path.join(store, '.git')):
        write_text(os.path.join(store, NOOP_CACHE), json.dumps({'code': CODE, 'hashes': sorted(hashes)}),
                   mode=0o600)


def run_all(store):
    """Import everything into the store and sweep it. The store's config.toml must be loaded (store.configure)."""
    common.apply_config()
    step('history: reading every tool\'s sessions')
    import_history.run(store)
    step('memory: importing')
    import_memory.run(store)
    step('instructions: copying')
    import_instructions.run(store)
    step('memory sources: learning secrets')
    # Learn from every memory source too: the memory importer skips sources that have not changed.
    for _, _, path in import_memory.claude_sources():
        redact(open(path, encoding='utf-8').read())
    for _, _, path, _ in import_memory.copilot_sources():
        redact(open(path, encoding='utf-8').read())
    # Pass 1 redacts and learns from what is in the store now (merged facts, hand-written memories);
    # pass 2 removes every copy of everything learned.
    step('store: masking secrets in every file')
    known, noop = load_noop(store), set()
    texts, read_as = {}, {}
    for path, text in store_texts(store):
        h = read_as[path] = digest(text)
        if h not in known:
            new = redact(text)
            if new != text:
                texts[path] = new
                continue
        noop.add(h)
        texts[path] = text
    step(f'store: {len(noop & known)} files skipped (no secrets found in them before), '
         f'{len(texts) - len(noop & known)} redacted, {len(texts) - len(noop)} of them changed')
    step(f'store: sweeping {len(texts)} files for {len(common.LEARNED)} learned secrets')
    changed = 0
    for path, text in texts.items():
        new = sweep(text)
        try:
            now = open(path, encoding='utf-8').read()
        except (OSError, UnicodeDecodeError):
            continue  # removed since it was read
        if digest(now) != read_as[path]:
            new = sweep(redact(now))  # edited since it was read (another session): keep that edit
        if new != now:
            write_text(path, new)
            changed += 1
            noop.discard(read_as[path])
    save_noop(store, noop)
    import_memory.rebuild_index(store)
    step('import done')
    print(f'sweep: {changed} files changed, {len(common.LEARNED)} secrets learned', file=sys.stderr)
    # Masked report: the values and pieces the sweep removed most often. A high count is a sign the
    # redactor learned an ordinary string; check it.
    mask = lambda v: f'{v[:3]}…(len {len(v)}) sha256:{hashlib.sha256(v.encode()).hexdigest()[:16]}'
    for label, counts in (('whole', common.SWEEP_COUNT), ('piece', common.PIECE_COUNT)):
        for v, n in sorted(counts.items(), key=lambda kv: -kv[1])[:15]:
            print(f'  {label} x{n:<5} [{common.LEARNED.get(v, "")} by {common.LEARNED_BY.get(v, "")}] {mask(v)}', file=sys.stderr)
