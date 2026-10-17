"""Built-in plugin: chats, memories and summaries. The core commands stay in cli.py."""
from .base import Plugin


class Memory(Plugin):
    name = 'memory'

    def capabilities(self, ctx):
        return ['memory.sync', 'memory.find', 'memory.summary']


plugin = Memory()
