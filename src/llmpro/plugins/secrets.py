"""Built-in plugin: the secret check, the hooks that run it, and the push guard for every repo."""
import os
import shlex
import sys

from .base import Plugin, SetupItem

LINES = ['[guard]', 'chain_to = "~/.local/share/llmpro/hooks"']


def _script_state(path):
    """-> 'missing', 'different' or 'ours' (the text, and the execute bit)."""
    from .. import pushguard
    try:
        text = open(path, encoding='utf-8').read()
        mode = os.stat(path).st_mode
    except (OSError, UnicodeDecodeError):
        return 'different' if os.path.exists(path) else 'missing'
    return 'ours' if text == pushguard.SCRIPT and mode & 0o111 else 'different'


def _guard_status():
    """-> (installed, chain_to as a real path or None, the raw chain_to, an error or '')."""
    from .. import gitpro
    try:
        _, obj = gitpro.get().run_json(['guard', 'status', '--json'])
    except gitpro.GitproError as exc:
        return None, None, None, str(exc)
    chain = obj.get('chain_to')
    chain = chain if isinstance(chain, str) and chain else None
    real = os.path.realpath(os.path.expanduser(chain)) if chain else None
    return obj.get('installed'), real, chain, ''


class PushGuard(SetupItem):
    name = 'push guard'

    def paths(self):
        from .. import pushguard
        return pushguard.hooks_dir(), os.path.join(pushguard.hooks_dir(), 'pre-push')

    def survey(self, ctx):
        from ..setup import Item, tilde
        folder, script = self.paths()
        have = _script_state(script)
        installed, real, raw, err = _guard_status()
        ours = os.path.realpath(folder)
        details, todo = [], []
        if have != 'ours':
            todo.append(f'write {tilde(script)} (mode 0755)')
        if err:
            state, details = 'gitpro guard status failed', [err]
        elif installed == 'none':
            state = 'gitpro guard not installed'
            details = ['run llmpro setup again after gitpro setup']
        elif real is None:
            state = 'chain_to not set'
            details = ['add these lines to ~/.config/gitpro.toml (llmpro never writes that file), then run gitpro setup:'] + LINES
            todo += details
        elif real == ours:
            state = 'in place'
        else:
            state = f'chain_to is {tilde(raw)}'
            details = ["gitpro runs that folder's pre-push, not llmpro's; to use both, call llmpro hook push-guard from it"]
        if have == 'missing':
            state = f'script missing; {state}'
        elif have == 'different':
            state = f'script differs; {state}'
        self.chain = real
        return Item(self.name, have != 'ours', state, details, todo)

    def apply(self, ctx, undo):
        from ..setup import tilde
        from .. import pushguard
        folder, script = self.paths()
        old = mode = None
        try:
            old = open(script, encoding='utf-8').read()
            mode = os.stat(script).st_mode & 0o777
        except (OSError, UnicodeDecodeError):
            pass
        os.makedirs(folder, exist_ok=True)
        with open(script, 'w', encoding='utf-8') as fh:
            fh.write(pushguard.SCRIPT)
        os.chmod(script, 0o755)
        if old is None:
            undo.run('rm', '-f', script)
        else:
            undo.raw(f'printf %s {shlex.quote(old)} > {shlex.quote(script)} && chmod {mode:o} {shlex.quote(script)}')
        print(f'[push guard] wrote {tilde(script)}', file=sys.stderr)
        if getattr(self, 'chain', None) is None and not _guard_status()[3]:
            print('[push guard] to turn it on, add to ~/.config/gitpro.toml (llmpro does not write that file):',
                  file=sys.stderr)
            for line in LINES:
                print(f'    {line}', file=sys.stderr)
            print('[push guard] then run: gitpro setup', file=sys.stderr)

    def _ours(self):
        _, script = self.paths()
        try:
            return script if 'llmpro push guard' in open(script, encoding='utf-8').read() else None
        except (OSError, UnicodeDecodeError):
            return None

    def _chain_is_ours(self):
        real = _guard_status()[1]
        return real is not None and real == os.path.realpath(self.paths()[0])

    def plan_remove(self, ctx):
        from ..setup import tilde
        script = self._ours()
        if not script:
            return []
        out = [f'remove {tilde(script)}']
        if self._chain_is_ours():
            out.append('the [guard] chain_to line in ~/.config/gitpro.toml can go too')
        return out

    def remove(self, ctx, undo):
        from ..setup import tilde
        script = self._ours()
        if not script:
            return []
        old = open(script, encoding='utf-8', errors='replace').read()
        mode = os.stat(script).st_mode & 0o777
        os.remove(script)
        undo.raw(f'printf %s {shlex.quote(old)} > {shlex.quote(script)} && chmod {mode:o} {shlex.quote(script)}')
        out = [f'removed {tilde(script)}']
        if self._chain_is_ours():
            out.append('the [guard] chain_to line in ~/.config/gitpro.toml can go too')
        return out


class Secrets(Plugin):
    name = 'secrets'

    def capabilities(self, ctx):
        return ['secrets.check', 'secrets.store-hooks']

    def setup_items(self, ctx):
        from .. import gitpro
        return [PushGuard()] if gitpro.get().has('guard') else []


plugin = Secrets()
