"""Locating the store, reading its config.toml, and the facts about this machine.

Nothing here is read when the module is imported: configure(path) loads the store's config.toml and fills
the module-level names below, in place, so `from .store import LOCAL_ONLY` keeps seeing the current list.
"""
import importlib
import os
import socket
import sys
import tomllib

MACHINE = socket.gethostname().split('.')[0]
DEFAULT_STORE = '~/.agents'

STORE = ''
CONFIG_FILE = ''
CONFIG = {}
LOCAL_ONLY = []  # folders from config.toml [privacy] local_only, ~ expanded, without a trailing slash
LOCAL_IDENTITIES = []  # gitpro identity ids from [privacy] local_identities; "default" is the default identity
NOW_LOCAL = []  # outputs of chats already shared that now place as local by identity (filled while importing)
QUIET = [False]  # progress lines off (-q)


def home():
    """The home folder, read each time so a test (or a user) can change HOME."""
    return os.path.expanduser('~')


def locate(arg=None):
    """The store folder: the --store argument, then $AGENTS_STORE, then ~/.agents."""
    return os.path.abspath(os.path.expanduser(arg or os.environ.get('AGENTS_STORE') or DEFAULT_STORE))


def data_file(store, name, legacy):
    """A data file of the store: .agentspro/<name>, else the old tools/<legacy> while the store still has it.
    -> the path to read (it may not exist)."""
    new = os.path.join(store, '.agentspro', name)
    old = os.path.join(store, 'tools', legacy)
    return old if not os.path.exists(new) and os.path.exists(old) else new


def configure(store, required=True):
    """Load STORE/config.toml. Without it local-only chats would be committed, so stop (required=True)."""
    global STORE, CONFIG_FILE, CONFIG
    STORE = store
    CONFIG_FILE = os.path.join(store, 'config.toml')
    CONFIG = {}
    try:
        with open(CONFIG_FILE, 'rb') as fh:
            CONFIG = tomllib.load(fh)
    except FileNotFoundError:
        if required:
            sys.exit(f'{CONFIG_FILE} is missing; the import tools need it')
    except tomllib.TOMLDecodeError as exc:
        sys.exit(f'{CONFIG_FILE}: {exc}')
    LOCAL_ONLY[:] = config_paths('privacy', 'local_only')
    ids = CONFIG.get('privacy', {}).get('local_identities', [])
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        sys.exit(f'{CONFIG_FILE}: [privacy] local_identities must be a list of identity ids')
    LOCAL_IDENTITIES[:] = ids
    privacy_begin()
    return CONFIG


def config_paths(section, key):
    """A list of folders from config.toml, ~ expanded, without a trailing slash."""
    value = CONFIG.get(section, {}).get(key, [])
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        sys.exit(f'{CONFIG_FILE}: [{section}] {key} must be a list of paths')
    return [os.path.expanduser(p).rstrip(os.sep) for p in value]


def config_list(section, key):
    """A list from config.toml, as it is written there."""
    value = CONFIG.get(section, {}).get(key, [])
    if not isinstance(value, list):
        sys.exit(f'{CONFIG_FILE}: [{section}] {key} must be a list')
    return value


def is_local_only(*paths):
    return any(p and (p == d or p.startswith(d + os.sep)) for p in paths for d in LOCAL_ONLY)


def is_local_project(folder):
    """Do the chats of this project folder stay in local/? True for a local_only folder, and, when
    [privacy] local_identities is set, for a repo gitpro places under one of those identities or cannot
    place. With local_identities empty this is is_local_only and privacy is not imported."""
    if is_local_only(folder):
        return True
    if not LOCAL_IDENTITIES:
        return False
    privacy = importlib.import_module('llmpro.privacy')
    return privacy.local_by_identity(folder)


def privacy_begin():
    """Start a run: forget the answers of the last one. Does nothing unless privacy was used."""
    NOW_LOCAL[:] = []
    mod = sys.modules.get('llmpro.privacy')
    if mod:
        mod.begin()


def privacy_end(write=True):
    """End a run: keep what gitpro answered (write=False for a dry run)."""
    mod = sys.modules.get('llmpro.privacy')
    if mod and write:
        mod.save_cache()


def privacy_stop_reason():
    """-> why sync cannot run with local_identities set (gitpro missing or too old), or ''."""
    if not LOCAL_IDENTITIES:
        return ''
    privacy = importlib.import_module('llmpro.privacy')
    return privacy.stop_reason()
