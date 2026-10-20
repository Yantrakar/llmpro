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
- Lessons: a feedback or project memory may carry `cue`, `evidence` and `confidence`.
  `llmpro check` fails a lesson that cites a chat that is not there, or a file under
  `local/` or `sessions/`. AGENTS.md and the skill ask for at most one lesson per
  session, and "confounded" rather than a guess. The idea comes from Google Research's
  harness2 (Apache 2.0); no code was copied.
- `[harness]` in config.toml names the parts of the harness the store holds
  (instructions, memory, skills; optionally guardrails, subagents, scripts, hooks).
  `llmpro setup -n` shows which tools get each part. It changes nothing.
- Each chat's frontmatter records `harness:`, the store commit current when the chat
  started. `find --json` history hits carry it. The first sync after upgrading adds it to
  every chat whose source is still on the machine: a one-time rewrite of those files.
- `llmpro find --shared-only` leaves out local-only chats.

### Changed

- The MCP `find` tool never returns local-only content: it always passes
  `--shared-only`, and a last check drops any hit under `local/` or `sessions/` (it
  says how many with `withheld`). `llmpro find` at the terminal still searches them.
- Every `llmpro sync` failure says what failed, what state the store is in, and what to
  run next. The desktop notice carries the whole message, under the app name llmpro.
- Tests no longer pop up desktop notices: `notify-send` is found like every other
  outside program.
- The project is now called llmpro, and so is the command. There is no `agentspro`
  command any more. Coming from agentspro: install llmpro, run `llmpro setup` (it
  rewrites the store's hooks and merge driver that still call `agentspro`), then
  `pipx uninstall agentspro`. If agentspro goes first, a commit in the store fails,
  because its hooks call a command that is gone; `llmpro setup` or `llmpro sync` fixes
  that. New undo scripts go to
  `$XDG_STATE_HOME/llmpro`; earlier ones are still found under the old name.
