"""llmpro: turn every AI assistant's chats, memories and instructions on a machine into one plain-text
store, keep secrets out of it, and keep it in step across machines."""

import os

# The version is written only in VERSION: the first word of its first line.
with open(os.path.join(os.path.dirname(__file__), 'VERSION'), encoding='utf-8') as _f:
    __version__ = _f.readline().split()[0]

# Raised whenever a change to the redactor (redact.py) can change what it writes. The store records the
# number it was written with, so a machine with an older tool can not rewrite what a newer one wrote.
REDACT_VERSION = 1
