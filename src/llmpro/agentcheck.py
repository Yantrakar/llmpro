"""`llmpro hook agent-check`: the Claude Code PreToolUse hook. It hands the event to `gitpro agent-check --agent
claude` when gitpro is there and has that capability, and does nothing otherwise.

It runs before every Bash call of the assistant, so it is lean (no argparse, no plugin registry, no
importlib.metadata) and it never gets in the way: any trouble of its own prints nothing and exits 0. What gitpro
itself answers, including its exit code, is passed through.

gitpro's version is not asked each time. It is kept in `gitpro-version.json` in llmpro's state folder, with the
program's path, size and modification time, and asked again only when one of those changes."""
import json
import os
import subprocess
import sys
import time

from . import gitpro

CAPABILITY = 'agent-check'
MAX_INPUT = 1_000_000  # bytes of the hook event
BUDGET = 8  # seconds for the whole hook, both gitpro calls: hooks.json stops it at 10 (spec 23)
CACHE = 'gitpro-version.json'


def cache_path():
    # The same folder as setup.state_dir(); not imported from there because setup is heavy.
    base = os.environ.get('XDG_STATE_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'state')
    return os.path.join(base, 'llmpro', CACHE)


def fingerprint(path):
    st = os.stat(path)
    return {'path': path, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns}


def left(deadline):
    """-> the seconds left until `deadline` (a time.monotonic() value); TimeoutError when none are."""
    s = deadline - time.monotonic()
    if s <= 0:
        raise TimeoutError
    return s


def read_capabilities(path, env, deadline):
    """-> the capabilities of the gitpro at `path` (empty when it is too old), or None when it does not answer."""
    try:
        r = subprocess.run([path, '--version', '--json'], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=left(deadline), env=env)
        data = json.loads(r.stdout)
        if gitpro.parse_version(data['version']) < gitpro.MIN_VERSION:
            return []
        return [str(c) for c in data['capabilities']]
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def capabilities(path, env, deadline):
    """The capabilities, from the cache while gitpro is unchanged."""
    want = fingerprint(path)
    try:
        with open(cache_path(), encoding='utf-8') as fh:
            data = json.load(fh)
        if (isinstance(data, dict) and {k: data.get(k) for k in want} == want
                and isinstance(data.get('capabilities'), list)):
            return data['capabilities']
    except (OSError, ValueError):
        pass
    caps = read_capabilities(path, env, deadline)
    if caps is None:
        return []  # not kept, so a gitpro that was slow once is asked again next time
    try:
        os.makedirs(os.path.dirname(cache_path()), mode=0o700, exist_ok=True)
        tmp = cache_path() + f'.{os.getpid()}'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump({**want, 'capabilities': caps}, fh)
        os.replace(tmp, cache_path())
    except OSError:
        pass
    return caps


def run(stdin=None):
    """-> the exit status. 0 whenever llmpro itself has trouble."""
    deadline = time.monotonic() + BUDGET
    try:
        g = gitpro.Gitpro()
        if not g.path:
            return 0
        env = g._env()
        if CAPABILITY not in capabilities(g.path, env, deadline):
            return 0
        data = (stdin or sys.stdin.buffer).read(MAX_INPUT)
        r = subprocess.run([g.path, CAPABILITY, '--agent', 'claude'], input=data, capture_output=True,
                           timeout=left(deadline), env=env)
        sys.stdout.buffer.write(r.stdout)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(r.stderr)
        sys.stderr.buffer.flush()
        return r.returncode if r.returncode >= 0 else 0
    except Exception:
        return 0
