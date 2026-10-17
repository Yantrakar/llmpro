"""llmpro: turn every AI assistant's chats, memories and instructions on a machine into one plain-text
store, keep secrets out of it, and keep it in step across machines."""

__version__ = "0.1.0"

# Raised whenever a change to the redactor (redact.py) can change what it writes. The store records the
# number it was written with, so a machine with an older tool can not rewrite what a newer one wrote.
REDACT_VERSION = 1
