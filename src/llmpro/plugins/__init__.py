"""The plugin registry. Not imported on the hook or merge-driver path."""
import sys

from .base import API, Context, Plugin, Status

GROUP = 'llmpro.plugins'
BUILTIN = ('memory', 'secrets', 'git')
_cache = []


def context(store=None):
    from .. import store as cfg
    return Context(store=cfg.locate(store), home=cfg.home())


def _builtins():
    import importlib
    return [(name, importlib.import_module(f'{__name__}.{name}').plugin) for name in BUILTIN]


def _warn(name, reason):
    print(f'llmpro: plugin {name} skipped: {reason}', file=sys.stderr)


def load():
    """-> every plugin, sorted by name; cached for the process."""
    if _cache:
        return _cache[0]
    from importlib.metadata import entry_points
    eps = list(entry_points(group=GROUP))
    if eps:
        found = []
        for ep in eps:
            try:
                found.append((ep.name, ep.load()))
            except Exception as exc:
                _warn(ep.name, f'cannot import it ({type(exc).__name__}: {exc})')
    else:  # a source tree without installed metadata
        found = _builtins()
    plugins, seen = [], set()
    for label, obj in found:
        if not isinstance(obj, Plugin):
            _warn(label, 'it is not a Plugin')
        elif obj.api != API:
            _warn(label, f'it speaks {obj.api}, not {API}')
        elif not obj.name or not isinstance(obj.name, str):
            _warn(label, 'it has no name')
        elif obj.name in seen:
            _warn(label, f'the name {obj.name} is already taken')
        else:
            seen.add(obj.name)
            plugins.append(obj)
    plugins.sort(key=lambda p: p.name)
    _cache.append(plugins)
    return plugins


def get(name):
    return next((p for p in load() if p.name == name), None)


def reset_cache():
    _cache.clear()


def describe(ctx):
    """-> [{"name", "api", "state", "reason", "hint"}] for --version --json and setup --json."""
    out = []
    for p in load():
        try:
            st = p.status(ctx)
        except Exception as exc:
            st = Status('error', f'{type(exc).__name__}: {exc}')
        out.append({'name': p.name, 'api': p.api, 'state': st.state, 'reason': st.reason, 'hint': st.hint})
    return out
