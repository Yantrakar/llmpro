"""Built-in plugin: gitpro, found through its command line (never imported)."""
import sys

from .. import gitpro
from .base import Plugin


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

    def mcp_child(self, ctx):
        g = gitpro.get()
        return [g.path, 'mcp'] if g.has('mcp') else None


plugin = Git()
