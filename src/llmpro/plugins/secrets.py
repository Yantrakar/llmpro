"""Built-in plugin: the secret check, the hooks that run it, and the push guard for every repo."""
import json
import os
import shlex
import shutil
import sys
import tomllib

from .base import Plugin, SetupItem

NEXT = 'then run llmpro setup again (it runs gitpro setup for you)'
REBUILD = ['setup', '-y', '--agent', 'none']
REBUILT = 'gitpro ' + ' '.join(REBUILD)


def lines_for(value):
    return ['[guard]', f'chain_to = {json.dumps(value)}']


def gitpro_config():
    """gitpro's config file, found as gitpro finds it (gitpro -h): $GITPRO_CONFIG, else ~/.config/gitpro.toml."""
    from .. import store
    return os.environ.get('GITPRO_CONFIG') or os.path.join(store.home(), '.config', 'gitpro.toml')


def _guard_headers(lines):
    return [i for i, line in enumerate(lines) if line.split('#', 1)[0].strip() == '[guard]']


def with_chain_to(text, value):
    """-> (text with `chain_to = value` first in its [guard] table, '') or (None, why it must be edited by hand)."""
    try:
        guard = tomllib.loads(text).get('guard')
    except tomllib.TOMLDecodeError as exc:
        return None, f'is not valid TOML ({exc})'
    if guard is not None and not isinstance(guard, dict):
        return None, 'has a guard key that is not a table'
    if guard and 'chain_to' in guard:
        return None, f'already sets chain_to = {json.dumps(guard["chain_to"])}'
    line = lines_for(value)[1] + '\n'
    lines = text.splitlines(keepends=True)
    heads = _guard_headers(lines)
    if guard is None:
        new = text + ('\n' if text and not text.endswith('\n') else '') + ('\n' if text else '') + '[guard]\n' + line
    elif len(heads) == 1:
        lines.insert(heads[0] + 1, line)
        new = ''.join(lines)
    else:
        return None, 'writes its [guard] table in a form llmpro does not edit'
    try:
        ok = tomllib.loads(new)['guard'].get('chain_to') == value
    except (tomllib.TOMLDecodeError, KeyError):
        ok = False
    return (new, '') if ok else (None, 'could not take the line where llmpro would put it')


def without_chain_to(text, value):
    """-> text without the `chain_to = value` line llmpro added, or None when there is no such line."""
    lines = text.splitlines(keepends=True)
    heads = _guard_headers(lines)
    if len(heads) != 1:
        return None
    want, head = lines_for(value)[1], heads[0]
    end = next((i for i in range(head + 1, len(lines)) if lines[i].lstrip().startswith('[')), len(lines))
    ours = [i for i in range(head + 1, end) if lines[i].strip() == want]
    if not ours:
        return None
    rest = lines[head + 1:ours[0]] + lines[ours[0] + 1:end]
    if any(line.strip() for line in rest):
        return ''.join(lines[:ours[0]] + lines[ours[0] + 1:])
    # the table held only our line: take out its header too, and the blank line setup put before it
    start = head - 1 if head and not lines[head - 1].strip() else head
    return ''.join(lines[:start] + lines[end:])


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


def _open_to_others(folder):
    try:
        return bool(os.stat(folder).st_mode & 0o022)
    except OSError:
        return False


def _rebuild(what):
    """Run gitpro setup so its hooks pick up the [guard] table; a SetupError says why when it fails."""
    from .. import gitpro
    from ..setup import SetupError
    try:
        code = gitpro.get().run(REBUILD)
    except gitpro.GitproError as exc:
        raise SetupError(f'{what}, but {exc}') from None
    if code != 0:
        raise SetupError(f'{what}, but {REBUILT} failed with exit status {code}; its output above says why')


class PushGuard(SetupItem):
    name = 'push guard'

    def paths(self):
        from .. import pushguard
        return pushguard.hooks_dir(), os.path.join(pushguard.hooks_dir(), 'pre-push')

    def value(self):
        from ..setup import tilde
        return tilde(self.paths()[0])

    def survey(self, ctx):
        from ..setup import Item, tilde
        folder, script = self.paths()
        have = _script_state(script)
        installed, real, raw, err = _guard_status()
        ours = os.path.realpath(folder)
        config = gitpro_config()
        details, todo, left = [], [], []
        self.chain, self.edit, self.private = real, None, False
        if have != 'ours':
            todo.append(f'write {tilde(script)} (mode 0755)')
        if _open_to_others(folder):
            self.private = True
            todo.append(f'chmod go-w {tilde(folder)} (gitpro refuses a chain_to folder others can write)')
        if err:
            state, details = 'gitpro guard status failed', [err, 'run gitpro guard status --json yourself to see why, '
                                                              'fix it, then run llmpro setup again']
        elif installed == 'none' or real is None:
            state = 'gitpro guard not installed' if installed == 'none' else 'chain_to not set'
            if installed == 'none':
                details.append('llmpro setup runs gitpro setup, which installs it')
            try:
                text = open(config, encoding='utf-8').read()
                new, why = with_chain_to(text, self.value())
            except FileNotFoundError:
                new, why = None, 'does not exist (gitpro setup writes it when it asks for your identities)'
            except (OSError, UnicodeDecodeError) as exc:
                new, why = None, f'could not be read ({exc.strerror if isinstance(exc, OSError) else exc})'
            if new is not None:
                self.edit = (config, text, new)
                todo += [f'add chain_to = {json.dumps(self.value())} to the [guard] table of {tilde(config)} '
                         f'(a copy is kept)', f'run {REBUILT}, so its hooks run the push guard']
            else:
                left = [f'{tilde(config)} {why}; llmpro left it. Add these lines to it by hand, {NEXT}:']
                left += ['  ' + line for line in lines_for(self.value())]
                details += left
        elif real == ours:
            state = 'in place'
        else:
            state = f'chain_to is {tilde(raw)}'
            details = ["gitpro runs that folder's pre-push, not llmpro's; to use both, call llmpro hook push-guard from it"]
        if have == 'missing':
            state = f'script missing; {state}'
        elif have == 'different':
            state = f'script differs; {state}'
        item = Item(self.name, bool(todo), state, todo + details, todo)
        item.left = left
        return item

    def apply(self, ctx, undo):
        from ..setup import tilde
        from .. import gitpro, pushguard
        folder, script = self.paths()
        old = mode = None
        try:
            old = open(script, encoding='utf-8').read()
            mode = os.stat(script).st_mode & 0o777
        except (OSError, UnicodeDecodeError):
            pass
        os.makedirs(folder, mode=0o755, exist_ok=True)
        with open(script, 'w', encoding='utf-8') as fh:
            fh.write(pushguard.SCRIPT)
        os.chmod(script, 0o755)
        if old is None:
            undo.run('rm', '-f', script)
        else:
            undo.raw(f'printf %s {shlex.quote(old)} > {shlex.quote(script)} && chmod {mode:o} {shlex.quote(script)}')
        print(f'[push guard] wrote {tilde(script)}', file=sys.stderr)
        if getattr(self, 'private', False) or _open_to_others(folder):
            was = os.stat(folder).st_mode & 0o777
            os.chmod(folder, was & ~0o022)
            undo.run('chmod', f'{was:o}', folder)
            print(f'[push guard] chmod go-w {tilde(folder)}', file=sys.stderr)
        edit = getattr(self, 'edit', None)
        if edit:
            config, text, new = edit
            backup = config + '.before-llmpro'
            shutil.copy2(config, backup)
            with open(config, 'w', encoding='utf-8') as fh:
                fh.write(new)
            undo.run(gitpro.get().path or 'gitpro', *REBUILD)
            undo.run('mv', '-f', backup, config)
            print(f'[push guard] added chain_to = {json.dumps(self.value())} to {tilde(config)} '
                  f'(the file as it was: {tilde(backup)})', file=sys.stderr)
            _rebuild(f'chain_to was added to {tilde(config)}')
            print(f'[push guard] ran {REBUILT}', file=sys.stderr)

    def _ours(self):
        _, script = self.paths()
        try:
            return script if 'llmpro push guard' in open(script, encoding='utf-8').read() else None
        except (OSError, UnicodeDecodeError):
            return None

    def _chain_is_ours(self):
        real = _guard_status()[1]
        return real is not None and real == os.path.realpath(self.paths()[0])

    def _our_line(self):
        """-> (config, text, text without llmpro's chain_to line), or None."""
        config = gitpro_config()
        try:
            text = open(config, encoding='utf-8').read()
        except (OSError, UnicodeDecodeError):
            return None
        new = without_chain_to(text, self.value())
        return (config, text, new) if new is not None else None

    def _left(self):
        from ..setup import tilde
        return (f'chain_to in {tilde(gitpro_config())} points at this folder, but llmpro did not write it; '
                f'take it out by hand, then run {REBUILT}')

    def plan_remove(self, ctx):
        from ..setup import tilde
        script = self._ours()
        if not script:
            return []
        out = [f'remove {tilde(script)}']
        if self._our_line():
            out.append(f'take chain_to out of the [guard] table of {tilde(gitpro_config())} and run {REBUILT}')
        elif self._chain_is_ours():
            out.append(self._left())
        return out

    def remove(self, ctx, undo):
        from ..setup import tilde
        from .. import gitpro
        script = self._ours()
        if not script:
            return []
        old = open(script, encoding='utf-8', errors='replace').read()
        mode = os.stat(script).st_mode & 0o777
        os.remove(script)
        undo.raw(f'printf %s {shlex.quote(old)} > {shlex.quote(script)} && chmod {mode:o} {shlex.quote(script)}')
        out = [f'removed {tilde(script)}']
        line = self._our_line()
        if line:
            config, text, new = line
            backup = config + '.before-llmpro'
            shutil.copy2(config, backup)
            with open(config, 'w', encoding='utf-8') as fh:
                fh.write(new)
            undo.run(gitpro.get().path or 'gitpro', *REBUILD)
            undo.run('mv', '-f', backup, config)
            out.append(f'took chain_to out of {tilde(config)} (the file as it was: {tilde(backup)})')
            _rebuild(f'chain_to was taken out of {tilde(config)}')
            out.append(f'ran {REBUILT}')
        elif self._chain_is_ours():
            out.append(self._left())
        return out


class Secrets(Plugin):
    name = 'secrets'

    def capabilities(self, ctx):
        return ['secrets.check', 'secrets.store-hooks']

    def setup_items(self, ctx):
        from .. import gitpro
        return [PushGuard()] if gitpro.get().has('guard') else []


plugin = Secrets()
