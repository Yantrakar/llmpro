"""The plugin contract, `llmpro-plugin.v1`. A plugin is an instance of `Plugin` found through the entry point
group `llmpro.plugins`. The three built-in plugins register the same way an outside package would."""
from dataclasses import dataclass

API = 'llmpro-plugin.v1'


@dataclass
class Status:
    state: str  # 'ok' | 'missing' | 'too_old' | 'error'
    reason: str = ''  # one line, for people
    hint: str = ''  # what to do, e.g. 'pipx install gitpro'


@dataclass
class Context:
    store: str  # the store folder (may not exist yet)
    home: str


class SetupItem:
    """One thing setup looks after, owned by a plugin."""
    name = ''

    def survey(self, ctx):
        """-> setup.Item(name, missing, state, details, todo)"""
        raise NotImplementedError

    def apply(self, ctx, undo):
        """Only called when survey() is missing. Raise setup.SetupError to stop."""

    def remove(self, ctx, undo):
        """For uninstall. -> the lines of what was done."""
        return []

    def plan_remove(self, ctx):
        """For uninstall -n. -> what remove() would do."""
        return []


class Plugin:
    name = ''
    api = API

    def status(self, ctx):
        return Status('ok')

    def capabilities(self, ctx):
        return []

    def add_commands(self, sub, common):
        """-> {command name: handler(args, store) -> int}"""
        return {}

    def setup_items(self, ctx):
        return []

    def mcp_child(self, ctx):
        """-> a command whose MCP tools llmpro serves as '<plugin name>_<tool>', or None."""
        return None
