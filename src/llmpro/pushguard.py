"""The push guard: `llmpro hook push-guard [remote] [url]`, run by git's pre-push hook (through gitpro's
`[guard] chain_to`). It checks the lines a push adds for secret formats and for values from the assistants'
raw logs, and stops the push on a hit. It fails open: any trouble of its own lets the push go on.

Kept light: `check` (and its pool, sqlite and hashing) is imported only when there are lines to scan."""
import os
import re
import subprocess
import sys
import time

from . import store as cfg

SCRIPT = """#!/bin/sh
# llmpro push guard; gitpro's pre-push hook runs this through [guard] chain_to
[ -n "${LLMPRO_SKIP:-}" ] && exit 0
command -v llmpro >/dev/null 2>&1 || exit 0
exec llmpro hook push-guard "$@"
"""
MAX_COMMITS = 1000
# Seconds for the whole guard. git opens the connection before pre-push runs, and the remote may close it while
# the hook works: a 16-minute rebuild of the leak cache lost a push to GitHub (2026-10-22). gitpro gives its own
# check 30 s. Past the budget the leak check matches with what the cache holds and says so.
BUDGET = 20
MAX_FILE = 1_000_000  # bytes of added lines per file and commit; more is skipped
ZERO = re.compile(r'0+')
HUNK = re.compile(r'@@ -\S+ \+(\d+)')


def hooks_dir():
    base = os.environ.get('XDG_DATA_HOME') or os.path.join(cfg.home(), '.local', 'share')
    return os.path.join(base, 'llmpro', 'hooks')


def git(*args, input=None):
    r = subprocess.run(['git', *args], input=input, capture_output=True, text=True, errors='replace')
    if r.returncode != 0:
        raise RuntimeError(f'git {args[0]} failed: {r.stderr.strip() or r.returncode}')
    return r.stdout


def commits_of(lines):
    """-> the commit ids a push adds, newest first, at most MAX_COMMITS; and whether there were more."""
    seen = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 4 or ZERO.fullmatch(parts[1]):
            continue  # not a ref line, or a delete
        local, remote = parts[1], parts[3]
        if ZERO.fullmatch(remote):
            out = git('rev-list', local, '--not', '--remotes')
        else:
            try:
                out = git('rev-list', f'{remote}..{local}')
            except RuntimeError:  # the remote's commit is not here (somebody else pushed): as a new branch
                out = git('rev-list', local, '--not', '--remotes')
        for sha in out.split():
            seen.setdefault(sha)
    shas = list(seen)
    return shas[:MAX_COMMITS], len(shas) > MAX_COMMITS


def added_texts(shas):
    """-> {label: text}, {label: (short sha, path, [line number in the new file for each line of the text])}."""
    out = git('log', '--no-walk=unsorted', '--stdin', '-p', '-U0', '--no-color', '--no-ext-diff', '--no-textconv',
              '--format=%x00%H', input='\n'.join(shas) + '\n')
    texts, where = {}, {}
    sha = path = None
    inhunk, n = False, 0
    for line in out.split('\n'):
        if line.startswith('\0'):
            sha, path, inhunk = line[1:], None, False
        elif line.startswith('diff --git '):
            path, inhunk = None, False
        elif not inhunk and line.startswith('+++ '):
            p = line[4:].split('\t')[0]
            if p == '/dev/null':
                path = None
            else:
                p = p[2:] if p.startswith('b/') else p
                path = p[1:-1] if p.startswith('"') and p.endswith('"') else p
        elif line.startswith('@@'):
            m = HUNK.match(line)
            inhunk, n = bool(m and path), int(m.group(1)) if m else 0
        elif inhunk and line.startswith('+'):
            label = f'{sha[:7]} {path}'
            if label not in texts:
                texts[label] = []
                where[label] = (sha[:7], path, [])
            texts[label].append(line[1:])
            where[label][2].append(n)
            n += 1
    result = {}
    for label, rows in texts.items():
        text = '\n'.join(rows)
        if len(text) <= MAX_FILE:
            result[label] = text
    return result, where


def _run(stdin):
    deadline = time.monotonic() + BUDGET
    store = cfg.locate(None)
    config = os.path.join(store, 'config.toml')
    if not os.path.exists(config):
        return 0
    open(config, 'rb').close()  # a store whose config cannot be read is broken: say so (and let the push go on)
    if git('rev-parse', '--is-bare-repository').strip() != 'true':  # a bare repo has no top folder, and isn't the store
        top = git('rev-parse', '--show-toplevel').strip()
        if os.path.realpath(top) == os.path.realpath(store):
            return 0  # the store has its own hooks
    shas, more = commits_of(stdin.read().splitlines())
    if not shas:
        return 0
    if more:
        print(f'llmpro: this push adds more than {MAX_COMMITS} commits; checking the newest {MAX_COMMITS}', file=sys.stderr)
    texts, where = added_texts(shas)
    if not texts:
        return 0
    # git hands a hook run in a bare repo GIT_DIR; the scan's git calls are about the store, not this repo
    for name in git('rev-parse', '--local-env-vars').split():
        os.environ.pop(name, None)
    from . import check
    late = []
    hits = check.scan_texts(store, texts, jobs=1, deadline=deadline, late=late)
    if late:
        print(f'llmpro: the leak check had no time to read {len(late)} raw logs in full; this push was checked '
              'against what the cache holds. Run llmpro check to bring the cache up to date.', file=sys.stderr)
    if not hits:
        return 0
    print('llmpro: push stopped, secrets in what this push adds:', file=sys.stderr)
    for h in hits:
        short, path, numbers = where[h.label]
        line = numbers[check.line_of(texts[h.label], h.offset) - 1]
        print(f'  {short} {path}:{line} [{h.kind}] {h.masked}', file=sys.stderr)
    print('To push anyway: LLMPRO_SKIP=1 git push. A false positive goes into the allowlist: llmpro check --help.',
          file=sys.stderr)
    return 1


def run(argv, stdin):
    """-> exit status: 0 let the push go on, 1 stop it."""
    if os.environ.get('LLMPRO_SKIP'):
        return 0
    try:
        return _run(stdin)
    except Exception as exc:  # the guard fails open
        print(f'llmpro: push guard could not run: {exc}; the push goes on', file=sys.stderr)
        return 0
