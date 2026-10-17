"""Privacy by git identity: a project folder whose repo gitpro places under one of the identities in
[privacy] local_identities keeps its chats in local/. Used only through store.is_local_project, and only
when local_identities is set. When in doubt the chat stays local.

gitpro is asked once per repo per run. The last answers are kept in $XDG_STATE_HOME/llmpro/privacy.json
(not in the store: identity ids are personal), so a folder that is later deleted is still placed.
"""
import datetime
import json
import os
import subprocess
import sys

from . import gitpro, setup, store as cfg

WHY_TIMEOUT = 20

_folders = {}  # project folder -> True/False, for this run
_tops = {}  # repo top folder -> identity id (None is the default identity), or an Exception
_asked = {}  # repo top folder -> identity id, for what gitpro answered this run (written to the cache)
_cache = []  # the cache file as read, once per run


def begin():
    _folders.clear()
    _tops.clear()
    _asked.clear()
    _cache.clear()


def cache_path():
    return os.path.join(setup.state_dir(), 'privacy.json')


def load_cache():
    if not _cache:
        try:
            with open(cache_path(), encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        _cache.append(data if isinstance(data, dict) else {})
    return _cache[0]


def save_cache():
    """Write the answers gitpro gave in this run, next to the older ones (atomic replace, mode 0600)."""
    if not _asked:
        return
    data = dict(load_cache())
    today = datetime.date.today().isoformat()
    for key, ident in _asked.items():
        data[key] = {'id': ident, 'checked': today}
    path = cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.tmp{os.getpid()}'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write('\n')
    os.replace(tmp, path)
    _asked.clear()
    _cache.clear()


def stop_reason():
    """-> '' when gitpro can answer `why`, else the sentence sync stops with."""
    g = gitpro.get()
    st = g.status()
    if st.state != 'ok':
        reason, hint = st.reason or st.state, st.hint
    elif not g.has('why'):
        reason, hint = 'gitpro has no why command', 'pipx upgrade gitpro'
    else:
        return ''
    return (f'[privacy] local_identities needs gitpro 0.4 or later ({reason}); {hint or "install or upgrade gitpro"}. '
            f'Or take local_identities out of config.toml.')


def short(path):
    home = cfg.home()
    return '~' + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def warn(folder, reason):
    print(f'llmpro: could not place {short(folder)} with gitpro ({reason}); its chats stay local', file=sys.stderr)


def same(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def top_of(folder):
    """-> the top folder of the repo that holds `folder`, or None when it is in no repo."""
    try:
        r = subprocess.run(['git', '-C', folder, 'rev-parse', '--show-toplevel'], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def identity_of(top):
    """-> the identity id gitpro expects for this repo (None is the default identity); raises ValueError."""
    if top not in _tops:
        try:
            code, obj = gitpro.get().run_json(['why', top, '--json'], timeout=WHY_TIMEOUT)
            if code not in (0, 1):
                raise ValueError(obj.get('error') or f'gitpro exit {code}')
            expected = obj.get('expected')
            if not isinstance(expected, dict):
                raise ValueError('no identity in its answer')
            ident = expected.get('id')
            if ident is not None and not isinstance(ident, str):
                raise ValueError('no identity in its answer')
            _tops[top] = ident
            _asked[top] = ident
        except (gitpro.GitproError, ValueError) as exc:
            _tops[top] = exc
    got = _tops[top]
    if isinstance(got, Exception):
        raise ValueError(str(got))
    return got


def listed(ident):
    return ('default' if ident is None else ident) in cfg.LOCAL_IDENTITIES


def cached(folder):
    """-> (found, id) from the cache for a folder or the nearest cached folder above it."""
    data = load_cache()
    path = folder
    while True:
        row = data.get(path)
        if isinstance(row, dict) and 'id' in row:
            return True, row['id']
        parent = os.path.dirname(path)
        if parent == path:
            return False, None
        path = parent


def local_by_identity(folder):
    """Do the chats of this project folder stay local because of the identity of its repo?"""
    if not folder:
        return False
    if folder not in _folders:
        _folders[folder] = decide(folder)
    return _folders[folder]


def decide(folder):
    store = cfg.STORE
    if folder == cfg.home() or (store and (folder == store or folder.startswith(store.rstrip(os.sep) + os.sep))):
        return False  # the home folder and the store hold global files, whether or not the folder is there
    if not os.path.isdir(folder):
        found, ident = cached(folder)
        if found:
            return listed(ident)
        warn(folder, 'folder is gone')
        return True
    top = top_of(folder)
    if top is None:
        return False
    if same(top, cfg.home()) or (cfg.STORE and same(top, cfg.STORE)):
        return False  # these hold global files, not a project
    try:
        return listed(identity_of(top))
    except ValueError as exc:
        warn(folder, str(exc))
        return True
