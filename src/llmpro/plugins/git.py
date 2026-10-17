"""Built-in plugin: gitpro, found through its command line (never imported)."""
import sys

from .. import gitpro
from .base import Plugin, SetupItem


def run_git(args, store):
    g = gitpro.get()
    st = g.status()
    if st.state != 'ok':
        print(f'llmpro git: {st.reason}. {st.hint}', file=sys.stderr)
        return 2
    try:
        return g.run(list(args.args))
    except gitpro.GitproError as exc:
        print(f'llmpro git: {exc}', file=sys.stderr)
        return 2


run_git.passthrough = True  # cli hands it everything after the command name, even a leading option


class GitproSetup(SetupItem):
    """What `gitpro setup` would do, shown in llmpro's one preview and run after the one yes (with --agent none:
    llmpro's own plugin registers the MCP server and the agent hook)."""
    name = 'gitpro'
    ASK = ['setup', '-n', '--json', '--agent', 'none']
    DO = ['setup', '-y', '--agent', 'none']

    def survey(self, ctx):
        from ..setup import Item
        try:
            code, data = gitpro.get().run_json(self.ASK, timeout=60)
        except gitpro.GitproError as exc:
            return Item(self.name, False, 'could not ask gitpro', [str(exc)])
        if code == 0:
            return Item(self.name, False, 'in place')
        entries = data.get('pending')
        if code != 1 or not isinstance(entries, list) or not entries:
            return Item(self.name, False, 'could not ask gitpro',
                        [f'gitpro setup -n answered with exit {code} and no pending list'])
        entries = [e for e in entries if isinstance(e, dict)]
        if any(e.get('group') == 'config' for e in entries):
            return Item(self.name, False, 'no identities yet', ['run gitpro setup in a terminal once'])
        todo = [f"{e.get('group', '?')}: {e.get('what', '?')}" for e in entries]
        return Item(self.name, True, f'{len(todo)} to do', todo, todo)

    def apply(self, ctx, undo):
        from ..setup import SetupError
        try:
            code = gitpro.get().run(self.DO)
        except gitpro.GitproError as exc:
            raise SetupError(str(exc)) from None
        if code != 0:
            raise SetupError(f'gitpro setup failed (exit {code}); see its output above')
        undo.note('gitpro setup ran; to take it back run: gitpro uninstall')

    def plan_remove(self, ctx):
        return ["gitpro's own setup is kept; gitpro uninstall removes it"]

    def remove(self, ctx, undo):
        return self.plan_remove(ctx)


class Git(Plugin):
    name = 'git'

    def status(self, ctx):
        return gitpro.get().status()

    def capabilities(self, ctx):
        g = gitpro.get()
        if g.status().state != 'ok':
            return []
        return ['git'] + [f'git.{c}' for c in sorted(g.capabilities)]

    def add_commands(self, sub, common):
        import argparse
        p = sub.add_parser('git', add_help=False,
                           help='run gitpro (identities, push guard, ...) with the terminal passed through')
        p.add_argument('args', nargs=argparse.REMAINDER, help='passed to gitpro unchanged')
        return {'git': run_git}

    def setup_items(self, ctx):
        g = gitpro.get()
        return [GitproSetup()] if g.status().state == 'ok' and g.has('setup.dry-run') else []

    def mcp_child(self, ctx):
        g = gitpro.get()
        return [g.path, 'mcp'] if g.has('mcp') else None


plugin = Git()
