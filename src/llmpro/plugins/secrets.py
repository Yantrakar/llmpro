"""Built-in plugin: the secret check and the hooks that run it."""
from .base import Plugin


class Secrets(Plugin):
    name = 'secrets'

    def capabilities(self, ctx):
        return ['secrets.check', 'secrets.store-hooks']


plugin = Secrets()
