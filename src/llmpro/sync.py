"""`llmpro sync`: tools/sync.sh in Python, same order and same checks.

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
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import llmpro
from . import check, store as cfg

HOOK_NAMES = ('pre-commit', 'pre-merge-commit', 'pre-push')
MERGE_DRIVER = 'llmpro merge-driver manifest %O %A %B'
# What stores set up before the rename say. The old command is gone, so sync and setup rewrite them;
# uninstall still knows them as llmpro's and removes them.
OLD_MERGE_DRIVER = 'agentspro merge-driver manifest %O %A %B'
OLD_HOOK_TEXT = '#!/bin/sh\nexec agentspro hook {name} "$@"\n'


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


def hooks_dir(store):
    """The folder git runs this store's hooks from (shared by its work trees)."""
    return os.path.join(os.path.realpath(out(store, 'rev-parse', '--path-format=absolute', '--git-common-dir')), 'hooks')


def hook_text(name):
    return f'#!/bin/sh\nexec llmpro hook {name} "$@"\n'


def hook_is_ours(name, text):
    """Is this the hook script llmpro writes, or the one it wrote under its old name? (For uninstall.)"""
    return text in (hook_text(name), OLD_HOOK_TEXT.format(name=name))


def repair_git_settings(store):
    """Settings git does not carry between clones: set them on every run, in this store only. The hooks are
    one-line scripts that run `llmpro hook <name>`; core.hooksPath is unset if it still points to the
    old tools/hooks."""
    hooks = hooks_dir(store)
    os.makedirs(hooks, exist_ok=True)
    for name in HOOK_NAMES:
        path = os.path.join(hooks, name)
        want = hook_text(name)
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
    driver = subprocess.run(['git', '-C', store, 'config', '--local', '--get', 'merge.manifest.driver'],
                            capture_output=True, text=True).stdout.strip()
    if driver != MERGE_DRIVER:
        git(store, 'config', '--local', 'merge.manifest.driver', MERGE_DRIVER)
    git(store, 'config', '--local', 'merge.ours.driver', 'true')


def store_redact_version():
    """[store] redact_version of the loaded config.toml; a missing number is 0."""
    value = cfg.CONFIG.get('store', {}).get('redact_version', 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SystemExit(f'{cfg.CONFIG_FILE}: [store] redact_version must be a whole number')
    return value


def too_new():
    """The store was written by a newer redactor than this tool has."""
    return store_redact_version() > llmpro.REDACT_VERSION


TOO_NEW_MESSAGE = 'upgrade llmpro: the store was written by a newer redactor'


def set_redact_version(path, number):
    """Write `redact_version = number` under [store] in config.toml, keeping the rest of the file as it is."""
    lines = open(path, encoding='utf-8').read().splitlines(keepends=True)
    in_store, header = False, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('['):
            in_store = s.split('#')[0].strip() == '[store]'
            header = i if in_store else header
        elif in_store and re.match(r'redact_version\s*=', s):
            lines[i] = f'redact_version = {number}\n'
            break
    else:
        if header is not None:
            lines.insert(header + 1, f'redact_version = {number}\n')
        else:
            if lines and not lines[-1].endswith('\n'):
                lines[-1] += '\n'
            lines += ['\n'] if lines else []
            lines += ['[store]\n', f'redact_version = {number}\n']
    with open(path, 'w', encoding='utf-8') as fh:
        fh.writelines(lines)


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
    if too_new():
        print(f'sync: {TOO_NEW_MESSAGE} (store {store_redact_version()}, this tool {llmpro.REDACT_VERSION})', file=sys.stderr)
        return 2
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
    cfg.privacy_begin()
    try:
        _run(store, local, say, hub)
        report_now_local(cfg.NOW_LOCAL)
        return 0
    except SyncFailed as exc:
        print(f'sync: {exc}', file=sys.stderr)
        notify(str(exc))
        return 1
    finally:
        hub.close()
        for s, h in old.items():
            signal.signal(s, h)
        cfg.privacy_end()
        lock.close()


def report_now_local(outputs, stream=None):
    """Chats already shared that now place as local by identity: say so, one line each, then a count."""
    stream = stream or sys.stderr
    for rel in outputs:
        print(f'already shared, now local by identity: {rel}; move it by hand (see FORMAT.md)', file=stream)
    if outputs:
        n = len(outputs)
        print(f'sync: {n} chat{"s" if n != 1 else ""} already shared, now local by identity', file=stream)


def _run(store, local, say, hub):
    reason = cfg.privacy_stop_reason()
    if reason:
        raise SyncFailed(reason)
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
    if store_redact_version() < llmpro.REDACT_VERSION:
        say(f'recording redact_version = {llmpro.REDACT_VERSION} in config.toml')
        set_redact_version(cfg.CONFIG_FILE, llmpro.REDACT_VERSION)
        cfg.CONFIG.setdefault('store', {})['redact_version'] = llmpro.REDACT_VERSION
    git(store, 'add', '-A')
    if not ok(store, 'diff', '--cached', '--quiet'):
        n = len(out(store, 'diff', '--cached', '--name-only').splitlines())
        say(f'committing {n} changed files (the hook runs the secret check)')
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        r = subprocess.run(['git', '-C', store, 'commit', '-q', '-m', f'sync {cfg.MACHINE} {stamp}: new and changed sessions'])
        if r.returncode != 0:
            raise SyncFailed(f'secret check failed; nothing committed. Run `llmpro check --context --store {store}`')

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


# Sources a person may have on the machine that no importer reads yet: (tool, folder under HOME).
UNREAD_SOURCES = (
    ('hermes', '.hermes'),
    ('copilot', '.vscode-server/data/User/workspaceStorage'),
)


def pending_sessions(store):
    """-> {tool: [new, changed]}: sessions whose source is not in the manifest, or whose size or time differ
    from the row written by the last import. Reads the sources' listings and stat() only."""
    import datetime
    from .importer import history
    from .redact import iso, load_manifest
    seen = {}
    for e in load_manifest(store).values():
        if e['machine'] == cfg.MACHINE:
            seen[e['source_path']] = e
    result = {}
    for tool, mod in history.TOOLS.items():
        new = changed = 0
        for src in mod.source_files():
            row = seen.get(str(src))
            if row is None:
                new += 1
                continue
            try:
                st = os.stat(src)
            except OSError:
                continue  # not a file (opencode rows live in one database)
            when = iso(datetime.datetime.fromtimestamp(st.st_mtime).astimezone())
            if st.st_size != row['size'] or when != row['mtime']:
                changed += 1
        result[tool] = [new, changed]
    return result


def hub_position(store):
    """-> (url or None, branch, ahead, behind), from the last fetch; nothing is fetched and nobody is logged in to."""
    url = subprocess.run(['git', '-C', store, 'remote', 'get-url', 'hub'], capture_output=True, text=True)
    if url.returncode != 0:
        return None, '', 0, 0
    branch = out(store, 'branch', '--show-current')
    r = subprocess.run(['git', '-C', store, 'rev-list', '--left-right', '--count', f'HEAD...hub/{branch}'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return url.stdout.strip(), branch, None, None  # never fetched
    ahead, behind = (int(n) for n in r.stdout.split())
    return url.stdout.strip(), branch, ahead, behind


def now_local_chats(store):
    """-> outputs of shared chats of this machine whose project now places as local by identity. Reads the
    manifest and the chats' frontmatter; asks gitpro (read-only); writes nothing."""
    if not cfg.LOCAL_IDENTITIES:
        return []
    from .redact import is_local_only, load_manifest, read_frontmatter
    found = []
    for row in load_manifest(store).values():
        out = row['output']
        if row['machine'] != cfg.MACHINE or row['kind'] != 'conversation' or not out or out.startswith('local/'):
            continue
        project = read_frontmatter(os.path.join(store, out))[0].get('project', '')
        if project and not is_local_only(row['source_path'], project) and cfg.is_local_project(project):
            found.append(out)
    return sorted(found)


def dry_run(store, as_json=False):
    """`sync -n`: what a sync would do. Changes nothing, logs in nowhere, fetches nothing. -> exit status."""
    from . import jsonout, setup
    check_store(store)
    have = store_redact_version()
    sessions = pending_sessions(store)
    url, branch, ahead, behind = hub_position(store)
    unread = [(t, p) for t, p in UNREAD_SOURCES if os.path.exists(os.path.join(cfg.home(), p))]
    settings = setup.git_settings_missing(store)
    refused = have > llmpro.REDACT_VERSION
    cfg.privacy_begin()
    stop = cfg.privacy_stop_reason()
    now_local = [] if stop else now_local_chats(store)
    if as_json:
        jsonout.emit('sync', {
            'redact_version': {'store': have, 'tool': llmpro.REDACT_VERSION, 'refused': refused},
            'git_settings_missing': settings,
            'sessions': {t: {'new': n, 'changed': c} for t, (n, c) in sessions.items()},
            'hub': None if url is None else {'url': url, 'branch': branch, 'ahead': ahead, 'behind': behind},
            'now_local': now_local,
            'stop_reason': stop or None,
            'unread_sources': [{'tool': t, 'path': p} for t, p in unread]}, ok=not refused and not stop)
        return 2 if refused else 1 if stop else 0
    print('llmpro sync -n: nothing is changed, nobody is logged in to, nothing is fetched')
    if refused:
        print(f'version     {TOO_NEW_MESSAGE} (store {have}, this tool {llmpro.REDACT_VERSION}); sync would stop')
    elif have < llmpro.REDACT_VERSION:
        print(f'version     sync would write redact_version = {llmpro.REDACT_VERSION} into config.toml (now {have})')
    else:
        print(f'version     redact_version {have}, as the tool')
    if stop:
        print(f'privacy     sync would stop: {stop}')
    print('git         ' + ('sync would repair: ' + ', '.join(settings) if settings else 'settings in place'))
    for tool, (n, c) in sessions.items():
        print(f'sessions    {tool:12} {n} new, {c} changed')
    if url is None:
        print('hub         no hub remote; sync would commit locally only')
    elif ahead is None:
        print(f'hub         {url}: not fetched yet, so ahead and behind are not known')
    else:
        print(f'hub         {url} ({branch}): {ahead} commits ahead, {behind} behind, as of the last fetch')
    for tool, path in unread:
        print(f'unread      ~/{path} ({tool}): found, no importer reads it')
    report_now_local(now_local, sys.stdout)
    return 2 if refused else 1 if stop else 0
