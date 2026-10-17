"""Setup items that register llmpro's MCP server with the assistants and point opencode at the store.

  mcp: claude code   `claude mcp add --scope user llmpro -- llmpro mcp`
  mcp: codex         `codex mcp add llmpro -- llmpro mcp`
  opencode config    the store's AGENTS.md in `instructions`, and llmpro under `mcp`, in opencode's config file

The assistants' own commands are the only way these files are touched: nothing here reads ~/.claude.json,
~/.claude/settings.json or Codex's config.toml. opencode's config can hold provider keys, so its content is
never printed or logged; it is read only to check and edit the two keys, and copied only to the backup.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys

from . import tools
from .plugins.base import SetupItem
from .setup import Item, SetupError, state_dir, tilde

TIMEOUT = 20  # seconds for one outside command
OPENCODE_MCP = {'type': 'local', 'command': ['llmpro', 'mcp'], 'enabled': True}
OPENCODE_SCHEMA = 'https://opencode.ai/config.json'
OPENCODE_FILES = ('opencode.json', 'opencode.jsonc')


def run_tool(cmd, timeout=None):
    """Run an outside command with no input, its output captured. -> (exit code, last line of stderr).
    The exit code is None when it timed out or could not be started, and the line says why."""
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=timeout or TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f'{os.path.basename(cmd[0])} timed out after {timeout or TIMEOUT:g} s'
    except OSError as exc:
        return None, f'{os.path.basename(cmd[0])} could not be run: {exc}'
    lines = [l for l in (r.stderr or '').splitlines() if l.strip()]
    return r.returncode, (lines[-1].strip() if lines else '')


def shown(words):
    return ' '.join(words)


# ---------------------------------------------------------------- Claude Code and Codex

class McpRegistration(SetupItem):
    def __init__(self, name, tool, scope):
        self.name, self.tool = name, tool
        self.scope = list(scope)  # ['--scope', 'user'] for Claude Code

    def _cmd(self, *words):
        return [tools.which(self.tool) or self.tool, *words]

    def _shown(self, *words):
        return shown([self.tool, *words])

    def add_words(self):
        return ['mcp', 'add', *self.scope, 'llmpro', '--', 'llmpro', 'mcp']

    def remove_words(self):
        return ['mcp', 'remove', *self.scope, 'llmpro']

    def registered(self, server):
        """-> (True/False, why not): does `<tool> mcp get SERVER` exit 0?"""
        code, line = run_tool(self._cmd('mcp', 'get', server))
        if code is None:
            return False, line
        return code == 0, ''

    def survey(self, ctx):
        present, why = self.registered('llmpro')
        details = [why] if why else []
        if self.registered('gitpro')[0]:
            details.append("gitpro's own MCP server is registered too; llmpro serves its tools as git_*. "
                           f'To remove it: {self._shown("mcp", "remove", "gitpro")}')
        if present:
            return Item(self.name, False, 'in place', details)
        return Item(self.name, True, 'missing', details, [self._shown(*self.add_words())])

    def apply(self, ctx, undo):
        code, line = run_tool(self._cmd(*self.add_words()))
        if code != 0:
            raise SetupError(f'{self._shown(*self.add_words())} failed' + (f': {line}' if line else ''))
        undo.run(self.tool, *self.remove_words())
        print(f'registered llmpro with {self.tool}', file=sys.stderr)

    def plan_remove(self, ctx):
        return [self._shown(*self.remove_words())] if self.registered('llmpro')[0] else []

    def remove(self, ctx, undo):
        if not self.registered('llmpro')[0]:
            return []
        code, line = run_tool(self._cmd(*self.remove_words()))
        if code != 0:
            raise SetupError(f'{self._shown(*self.remove_words())} failed' + (f': {line}' if line else ''))
        undo.run(self.tool, *self.add_words())
        return [self._shown(*self.remove_words())]


# ---------------------------------------------------------------- opencode

def opencode_dir(ctx):
    return os.path.join(ctx.home, '.config', 'opencode')


def agents_path(ctx):
    return os.path.join(ctx.store, 'AGENTS.md')


def make_backup_dir():
    base = state_dir()
    os.makedirs(base, mode=0o700, exist_ok=True)
    os.chmod(base, 0o700)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    path, n = os.path.join(base, f'backup-{stamp}'), 1
    while True:
        try:
            os.mkdir(path, 0o700)
            return path
        except FileExistsError:
            n += 1
            path = os.path.join(base, f'backup-{stamp}-{n}')


class OpencodeConfig(SetupItem):
    name = 'opencode config'

    def file(self, ctx):
        """-> the path of the config file, or None when there is none."""
        for name in OPENCODE_FILES:
            p = os.path.join(opencode_dir(ctx), name)
            if os.path.isfile(p):
                return p
        return None

    def load(self, path):
        """-> the parsed object when the file is plain JSON of the shape we can edit, else None.
        Nothing from the file is ever shown."""
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.loads(fh.read())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        if 'instructions' in data and not isinstance(data['instructions'], list):
            return None
        if 'mcp' in data and not isinstance(data['mcp'], dict):
            return None
        return data

    def lacking(self, data, want):
        out = []
        if want not in (data.get('instructions') or []):
            out.append('instructions lacks the store AGENTS.md')
        if (data.get('mcp') or {}).get('llmpro') != OPENCODE_MCP:
            out.append('mcp lacks llmpro')
        return out

    def by_hand(self, ctx):
        return ['add this path to "instructions": ' + json.dumps(agents_path(ctx)),
                'add this to "mcp": ' + json.dumps({'llmpro': OPENCODE_MCP})]

    def survey(self, ctx):
        path = self.file(ctx)
        want = agents_path(ctx)
        if path is None:
            return Item(self.name, True, 'missing', ['no opencode config file yet'],
                        [f'create {tilde(os.path.join(opencode_dir(ctx), "opencode.json"))} with the store AGENTS.md and the llmpro MCP server'])
        data = self.load(path)
        if data is None:
            return Item(self.name, True, 'missing', [f'{tilde(path)} is not plain JSON (or has an unexpected shape); llmpro does not edit it'],
                        [f'{tilde(path)}: llmpro will not edit it; add by hand'] + self.by_hand(ctx))
        lacking = self.lacking(data, want)
        if not lacking:
            return Item(self.name, False, 'in place', [tilde(path)])
        return Item(self.name, True, 'missing', [f'{tilde(path)}: ' + ', '.join(lacking)],
                    [f'edit {tilde(path)}: ' + ', '.join(l.replace('lacks', 'add to') for l in lacking) + ' (a backup is kept)'])

    def write(self, path, data, mode=0o644):
        tmp = path + '.llmpro-tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
        os.chmod(tmp, mode)
        os.replace(tmp, path)

    def backed_up_write(self, path, data, undo):
        backup = os.path.join(make_backup_dir(), os.path.basename(path))
        shutil.copy2(path, backup)
        mode = os.stat(path).st_mode & 0o777
        self.write(path, data, mode)
        undo.run('cp', '-p', backup, path)

    def apply(self, ctx, undo):
        path, want = self.file(ctx), agents_path(ctx)
        if path is None:
            path = os.path.join(opencode_dir(ctx), 'opencode.json')
            self.write(path, {'$schema': OPENCODE_SCHEMA, 'instructions': [want], 'mcp': {'llmpro': OPENCODE_MCP}})
            undo.run('rm', '-f', path)
            print(f'wrote     {tilde(path)}', file=sys.stderr)
            return
        data = self.load(path)
        if data is None:
            print(f'llmpro does not edit {tilde(path)}; add these by hand:', file=sys.stderr)
            for line in self.by_hand(ctx):
                print(f'  {line}', file=sys.stderr)
            return
        if want not in (data.get('instructions') or []):
            data['instructions'] = [*(data.get('instructions') or []), want]
        if data.get('mcp', {}).get('llmpro') != OPENCODE_MCP:
            data.setdefault('mcp', {})['llmpro'] = dict(OPENCODE_MCP)
        self.backed_up_write(path, data, undo)
        print(f'edited    {tilde(path)}', file=sys.stderr)

    def _removal(self, ctx):
        """-> (path, data or None, whether llmpro's lines are in it)."""
        path = self.file(ctx)
        if path is None:
            return None, None, False
        data = self.load(path)
        want = agents_path(ctx)
        if data is not None:
            return path, data, want in (data.get('instructions') or []) or 'llmpro' in (data.get('mcp') or {})
        try:
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            return path, None, False
        return path, None, want in text or '"llmpro"' in text

    def plan_remove(self, ctx):
        path, data, found = self._removal(ctx)
        if not found:
            return []
        if data is None:
            return [f'{tilde(path)} is not plain JSON; take these out by hand: {json.dumps(agents_path(ctx))} in "instructions", and "llmpro" in "mcp"']
        return [f'take llmpro out of {tilde(path)} (a backup is kept)']

    def remove(self, ctx, undo):
        path, data, found = self._removal(ctx)
        if not found:
            return []
        want = agents_path(ctx)
        if data is None:
            return [f'{tilde(path)} is not plain JSON, so it was not edited; take these out by hand: '
                    f'{json.dumps(want)} in "instructions", and "llmpro" in "mcp"']
        if 'instructions' in data:
            data['instructions'] = [i for i in data['instructions'] if i != want]
            if not data['instructions']:
                del data['instructions']
        if 'mcp' in data:
            data['mcp'].pop('llmpro', None)
            if not data['mcp']:
                del data['mcp']
        if not [k for k in data if k != '$schema']:  # only what setup wrote is left: the file goes
            backup = os.path.join(make_backup_dir(), os.path.basename(path))
            shutil.copy2(path, backup)
            os.remove(path)
            undo.run('cp', '-p', backup, path)
            return [f'removed {tilde(path)} (it held only llmpro\'s lines)']
        self.backed_up_write(path, data, undo)
        return [f'took llmpro out of {tilde(path)}']


def items(ctx):
    out = []
    if tools.which('claude'):
        out.append(McpRegistration('mcp: claude code', 'claude', ['--scope', 'user']))
    if tools.which('codex'):
        out.append(McpRegistration('mcp: codex', 'codex', []))
    if os.path.isdir(opencode_dir(ctx)):
        out.append(OpencodeConfig())
    return out
