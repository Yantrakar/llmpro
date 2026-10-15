"""`agentspro sync`: tools/sync.sh in Python, same order and same checks.

Import new and changed sessions from every tool on this machine, commit them, then exchange commits with
the hub repo (remote "hub", a bare repo on one of the person's machines) if one is set up.

Every commit goes through the pre-commit hook and every push through the pre-push hook, so nothing is
committed or sent unless both secret checks pass. Commits fetched from the hub are checked (files and every
blob) before they are merged. sessions/ is git-ignored and never leaves this machine.

  sync            import, commit, fetch, check, merge, push
  sync --local    import and commit only
"""
import datetime
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from . import check, store as cfg

HOOK_NAMES = ('pre-commit', 'pre-merge-commit', 'pre-push')
MERGE_DRIVER = 'agentspro merge-driver manifest %O %A %B'


class SyncFailed(Exception):
    """A step failed: the message goes to stderr (and a desktop notification), the exit status is 1."""


def git(store, *args, check=True, capture=True, input=None):
    return subprocess.run(['git', '-C', store, *args], capture_output=capture, text=True, check=check, input=input)


def out(store, *args):
    return git(store, *args).stdout.strip()


def ok(store, *args):
    """Did the git command succeed? Its output is dropped."""
    return subprocess.run(['git', '-C', store, *args], capture_output=True).returncode == 0


def check_store(store):
    """The store must be the top of its own git work tree: every git setting below goes to this repository
    and to no other one that happens to contain the folder."""
    if not os.path.isdir(store):
        raise SystemExit(f'{store}: no such folder')
    top = subprocess.run(['git', '-C', store, 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(store):
        raise SystemExit(f'{store} is not the top of a git repository; not touching git settings there')


def repair_git_settings(store):
    """Settings git does not carry between clones: set them on every run, in this store only. The hooks are
    one-line scripts that run `agentspro hook <name>`; core.hooksPath is unset if it still points to the
    old tools/hooks."""
    hooks = os.path.join(os.path.realpath(out(store, 'rev-parse', '--path-format=absolute', '--git-common-dir')), 'hooks')
    os.makedirs(hooks, exist_ok=True)
    for name in HOOK_NAMES:
        path = os.path.join(hooks, name)
        want = f'#!/bin/sh\nexec agentspro hook {name} "$@"\n'
        try:
            have = open(path, encoding='utf-8').read()
        except OSError:
            have = None
        if have != want:
            tmp = f'{path}.tmp{os.getpid()}'
            with open(tmp, 'w', encoding='utf-8') as fh:
                fh.write(want)
            os.chmod(tmp, 0o755)
            os.replace(tmp, path)
    cur = subprocess.run(['git', '-C', store, 'config', '--local', '--get', 'core.hooksPath'], capture_output=True, text=True)
    if cur.returncode == 0 and cur.stdout.strip().rstrip('/').endswith('tools/hooks'):
        git(store, 'config', '--local', '--unset', 'core.hooksPath')
    git(store, 'config', '--local', 'merge.manifest.driver', MERGE_DRIVER)
    git(store, 'config', '--local', 'merge.ours.driver', 'true')


class Hub:
    """A hub reached over ssh: log in once, with the password, and share that login for the fetch and the
    push (ssh ControlMaster). It is closed when sync ends, however it ends: no key is installed and no
    login outlives the sync."""

    def __init__(self):
        self.ctl = self.dest = ''

    @staticmethod
    def destination(url):
        if url.startswith(('/', 'file://')):
            return ''
        if url.startswith('ssh://'):
            return 'ssh://' + url[len('ssh://'):].split('/', 1)[0]
        if '://' in url:
            return ''  # http(s) and the like: no ssh login to share
        if ':' in url:
            return url.split(':', 1)[0]
        return ''

    def open(self, url, say):
        self.dest = self.destination(url)
        if not self.dest:
            return
        self.ctl = tempfile.mkdtemp()  # mode 700: only this user can use the shared login
        say(f'logging in to the hub ({self.dest}) for this sync')
        r = subprocess.run(['ssh', '-fN', '-o', 'ControlMaster=yes', '-o', f'ControlPath={self.ctl}/c', self.dest])
        if r.returncode != 0:
            raise SyncFailed('cannot log in to the hub')
        os.environ['GIT_SSH_COMMAND'] = f'ssh -o ControlMaster=no -o ControlPath={self.ctl}/c'

    def close(self):
        if not self.ctl:
            return
        subprocess.run(['ssh', '-o', f'ControlPath={self.ctl}/c', '-O', 'exit', self.dest],
                       stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        shutil.rmtree(self.ctl, ignore_errors=True)
        self.ctl = ''


def notify(msg):
    if shutil.which('notify-send'):
        subprocess.run(['notify-send', '.agents sync', msg], capture_output=True)


def run(store, local=False, quiet=False):
    """-> exit status: 0 done, 1 a step failed (message on stderr). The store's config.toml must be loaded."""
    check_store(store)
    start = time.time()

    def say(msg):
        if not quiet:
            print(f'[{int(time.time() - start)}s] sync: {msg}', file=sys.stderr, flush=True)

    # One sync at a time: two at once would write the same files and race on the commit.
    lock = open(os.path.join(os.path.realpath(out(store, 'rev-parse', '--path-format=absolute', '--git-dir')), 'sync.lock'), 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f'sync: another sync is running in {store}', file=sys.stderr)
        notify(f'another sync is running in {store}')
        return 1

    hub = Hub()

    def term(signum, frame):
        raise SystemExit(1)
    old = {s: signal.signal(s, term) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        _run(store, local, say, hub)
        return 0
    except SyncFailed as exc:
        print(f'sync: {exc}', file=sys.stderr)
        notify(str(exc))
        return 1
    finally:
        hub.close()
        for s, h in old.items():
            signal.signal(s, h)
        lock.close()


def _run(store, local, say, hub):
    repair_git_settings(store)
    url = subprocess.run(['git', '-C', store, 'remote', 'get-url', 'hub'], capture_output=True, text=True)
    if not local and url.returncode == 0:
        hub.open(url.stdout.strip(), say)

    say('importing sessions, memories and instructions')
    from .importer import all as import_all
    try:
        import_all.run_all(store)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            print(exc.code, file=sys.stderr)
        raise SyncFailed('import failed')
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        raise SyncFailed('import failed')
    git(store, 'add', '-A')
    if not ok(store, 'diff', '--cached', '--quiet'):
        n = len(out(store, 'diff', '--cached', '--name-only').splitlines())
        say(f'committing {n} changed files (the hook runs the secret check)')
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        r = subprocess.run(['git', '-C', store, 'commit', '-q', '-m', f'sync {cfg.MACHINE} {stamp}: new and changed sessions'])
        if r.returncode != 0:
            raise SyncFailed(f'secret check failed; nothing committed. Run `agentspro check --context --store {store}`')

    if local:
        return
    if not ok(store, 'remote', 'get-url', 'hub'):
        print('sync: no hub remote; committed locally only', file=sys.stderr)
        return

    branch = out(store, 'branch', '--show-current')
    say('fetching from the hub')
    if subprocess.run(['git', '-C', store, 'fetch', '-q', 'hub']).returncode != 0:
        raise SyncFailed('cannot reach the hub')
    if not ok(store, 'rev-parse', '-q', '--verify', f'hub/{branch}') or ok(store, 'merge-base', '--is-ancestor', f'hub/{branch}', 'HEAD'):
        pass  # hub is empty or has nothing new
    else:
        # Check what came in before merging it: every file and every blob in its history.
        say('checking the fetched commits for secrets')
        if check.run(store, history=True) != 0:
            raise SyncFailed('fetched commits failed the secret check; not merged')
        say("merging the hub's commits")
        if subprocess.run(['git', '-C', store, 'merge', '-q', '--no-edit', f'hub/{branch}']).returncode != 0:
            subprocess.run(['git', '-C', store, 'merge', '--abort'])
            raise SyncFailed('merge conflict with the hub (probably a memory edited on two machines); merge by hand')
        from .importer import memory
        memory.run(store, index_only=True)
        git(store, 'add', 'memory/INDEX.md')
        if not ok(store, 'diff', '--cached', '--quiet'):
            r = subprocess.run(['git', '-C', store, 'commit', '-q', '-m', f'sync {cfg.MACHINE}: rebuild memory index after merge'])
            if r.returncode != 0:
                raise SyncFailed('secret check failed after merge')
    ahead = subprocess.run(['git', '-C', store, 'rev-list', '--count', f'hub/{branch}..HEAD'], capture_output=True, text=True)
    say(f'pushing {ahead.stdout.strip() if ahead.returncode == 0 else "all"} commits (the hook checks every blob in history)')
    if subprocess.run(['git', '-C', store, 'push', '-q', 'hub', f'HEAD:{branch}']).returncode != 0:
        raise SyncFailed('push to the hub failed')
    say('done')
