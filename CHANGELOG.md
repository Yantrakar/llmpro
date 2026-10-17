# Changelog

All notable changes to llmpro (developed as agentspro). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). A release needs a section
`## X.Y.Z (YYYY-MM-DD)` for its version; see docs/releasing.md.

## 0.1.0 (unreleased)

### Added

- `llmpro sync` imports the chats, memories and instruction files of Claude Code,
  Codex, Copilot, Copilot CLI and opencode into one git store, with secrets redacted,
  commits them and exchanges commits with the hub. `--local` leaves the hub out, and
  `-n` shows what a sync would do.
- `llmpro setup` links each tool's folders to the store and installs the git hooks.
  `-n` shows what is missing. `llmpro uninstall` takes llmpro off a machine and
  keeps the store.
- `llmpro check` scans the store for secrets, and checks it against the secret
  values in the tools' own logs. `--staged`, `--history` and `--near-miss` change what
  it looks at; `--fix` rebuilds `memory/INDEX.md`.
- `llmpro find` searches past chats and memories.
- `llmpro summary` sets the summary at the top of a conversation.
- `llmpro mcp` serves `find`, `check` and `sync_status` to assistants over stdio.
- `llmpro --version --json` prints the version and the capabilities, so other tools can
  check what this llmpro supports. Every `--json` output carries `ok` and `command`.
- Plugins: llmpro finds plugins through the `llmpro.plugins` entry point group. The
  built-in ones are `git`, `memory` and `secrets`. Setup shows each plugin's state.
- gitpro support. llmpro finds the `gitpro` program (0.4.0 or later; `$LLMPRO_GITPRO`
  overrides the path) and never imports it. gitpro stays a separate tool.
  - `llmpro git ...` runs gitpro with the same arguments.
  - `llmpro mcp` also serves gitpro's read-only tools, named `git_*`.
  - `llmpro setup` runs `gitpro setup` in the same preview and question, and
    `llmpro uninstall` leaves gitpro's setup in place.
- `llmpro setup` registers the MCP server with Claude Code and Codex through their own
  `mcp add` commands, and adds the store's `AGENTS.md` and the MCP server to opencode's
  config (after a backup; a config with comments is left for the user to edit).
- A push guard for every repo: with gitpro's global hooks, `[guard] chain_to` can point
  at llmpro's hook folder, and each push is then checked for secrets in the lines it
  adds. llmpro never writes `gitpro.toml`; setup prints the lines to add. `LLMPRO_SKIP=1`
  lets one push through.
- `[privacy] local_identities` in `config.toml`: chats of repos that gitpro places under
  one of those identities stay in `local/`. When gitpro cannot place a folder, its
  chats stay local. A chat that was shared before is listed, not moved.
- The `llmpro` skill, for every assistant: setup writes it to the store's `skills/` and
  keeps it up to date, and leaves a copy alone once its first marker line is taken out.
- A Claude Code plugin in this repo, with the skill, the MCP server and a hook before each
  Bash call that asks gitpro (when it can) whether the git identity is right. Install it
  with `/plugin marketplace add Yantrakar/llmpro`, then `/plugin install llmpro@llmpro`.

### Changed

- The project is now called llmpro. The `agentspro` command keeps working for now and
  says on stderr that the name changed. Hooks and merge drivers set up under the old
  name are still recognised. New undo scripts go to `$XDG_STATE_HOME/llmpro`; earlier
  ones are still found under the old name.
