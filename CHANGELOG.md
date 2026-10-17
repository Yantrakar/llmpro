# Changelog

All notable changes to agentspro (to be published as llmpro). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). A release needs a section
`## X.Y.Z (YYYY-MM-DD)` for its version; see docs/releasing.md.

## 0.1.0 (unreleased)

### Added

- `agentspro sync` imports the chats, memories and instruction files of Claude Code,
  Codex, Copilot, Copilot CLI and opencode into one git store, with secrets redacted,
  commits them and exchanges commits with the hub. `--local` leaves the hub out, and
  `-n` shows what a sync would do.
- `agentspro setup` links each tool's folders to the store and installs the git hooks.
  `-n` shows what is missing. `agentspro uninstall` takes agentspro off a machine and
  keeps the store.
- `agentspro check` scans the store for secrets, and checks it against the secret
  values in the tools' own logs. `--staged`, `--history` and `--near-miss` change what
  it looks at; `--fix` rebuilds `memory/INDEX.md`.
- `agentspro find` searches past chats and memories.
- `agentspro summary` sets the summary at the top of a conversation.
- `agentspro mcp` serves `find`, `check` and `sync_status` to assistants over stdio.
