# Changelog

All notable changes to llmpro (developed as agentspro). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). A release needs a section
`## X.Y.Z (YYYY-MM-DD)` for its version; see docs/releasing.md.

## 0.1.3 (2026-10-23)

### Fixed

- HTTP Basic credentials (`Basic` and base64 of `user:password`) are masked as
  `[REDACTED:auth-header]` wherever they appear, not only after an `Authorization`
  key, and the password inside is masked too. `llmpro check` and the push guard
  report them. A sync stopped by the check on one such line now goes through.

### Added

- `.agentspro/blocklist.json` in the store, `{"<fingerprint>": "reason"}` like the
  allowlist, for a secret no rule can find. The sync masks a listed value as
  `[REDACTED:listed]`, and `llmpro check` and the push guard report it. The file is
  committed and reaches every machine; a fingerprint of a short or common password
  can be guessed back, so list only long random values. A new or changed list makes
  the next sync redact every file again, and `check --history` check every blob again.

## 0.1.2 (2026-10-23)

### Fixed

- The push guard keeps to a time budget of 20 seconds. A push could wait many minutes
  while the leak check read every raw log again, until the remote closed the
  connection and the push failed. Past the budget, the leak check matches what the
  cache holds, says so, and saves what it read, so the next push goes on from there;
  the check of secret formats always runs in full. `llmpro check` brings the cache up
  to date.
- The leak cache is kept across releases. It was dropped at every upgrade, so the first
  check after one read every raw log again. It is dropped only when the rules for what
  is collected change. The first check after upgrading to 0.1.2 still reads them once.
- The MCP `check` tool reads raw logs for 20 seconds at most and is stopped after 60, so
  the assistant is no longer left waiting for every other answer. Its result carries
  `late`, the raw logs not read in full in the time.
- The Claude Code hook before each Bash call keeps to one budget of 8 seconds for both
  of its gitpro calls. It could take 16, and Claude Code stops it at 10.
- The sync's no-op cache is kept across releases unless the redaction code changed,
  and is written readable by you only (it was readable by others). The first sync after
  upgrading to 0.1.2 still redacts every file once.

### Added

- `llmpro check --budget SECONDS` reads raw logs for the leak check only this long and
  checks the rest against the cache; the output says how many were not read in full.

## 0.1.1 (2026-10-22)

### Changed

- Releases are run by releasepro: a person says yes at the gate of the GitHub
  release and of PyPI, and a job that failed can be run again. The GitHub release
  carries the release notes and an announcement draft.
- The version is written in one file, `src/llmpro/VERSION`. The Claude Code plugin
  no longer names a version, so Claude Code sees an update from the commit.

## 0.1.0 (2026-10-22)

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
  adds. setup adds that line to gitpro's config itself (after a copy), runs
  `gitpro setup -y --agent none`, and uninstall takes the line out. `LLMPRO_SKIP=1`
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
- `[harness]` in config.toml names the seven parts of the harness the store holds:
  instructions, memory, skills, subagents, hooks, guardrails and scripts.
  `llmpro setup -n` shows which tools get each part, and `llmpro setup` places them:
  - it creates a missing part folder with a README;
  - it links each tool's subagents, hooks and skills folder to the tool's own folder in the
    store, moving files already there into the store first and leaving a name clash
    alone;
  - it puts executable scripts on PATH through `~/.local/bin`;
  - it installs Claude Code's hooks and guardrails as two plugins from a local
    marketplace in the store (`llmpro-harness`), with `claude plugin`.

  `llmpro uninstall` replaces each link with a copy and removes the two plugins.
- Each chat's frontmatter records `harness:`, the store commit current when the chat
  started. `find --json` history hits carry it. The first sync after upgrading adds it to
  every chat whose source is still on the machine: a one-time rewrite of those files.
- `llmpro find --shared-only` leaves out local-only chats.
- `llmpro resume` lists the open Claude Code, Copilot CLI and opencode sessions on this
  machine, those waiting for you first, with how long each has waited and one line of
  what it said. It reads each tool's own records and writes nothing. `--json` gives the
  same list as data, and `--no-line` leaves out every line. Live sessions need Linux.
- `[machines]` in config.toml names other machines. `llmpro resume` asks them all at once
  over ssh, without a password, and lists their sessions too; `--local` asks none.
  `setup -n` says whether each machine can be asked. The MCP server gains a read-only
  `sessions` tool, which leaves out every content line and every local-only session.
- `llmpro resume TARGET` continues a session, by number, name or id, here or on its
  machine over ssh. A session open elsewhere is refused, and two llmpro cannot continue
  the same one at once. The tool runs in the session's folder without the variables
  that tell it it runs inside another tool, and llmpro says if it changed an editor
  settings file. `-n` shows what it would run.
- `llmpro resume --watch` keeps the list current and lets you pick a session from it. A
  desktop notice says when a session starts waiting for you; a session in a local-only
  folder is not named in it. Other machines are asked over one shared ssh login each.
  `[sessions] refresh` and `notify` in config.toml set the pace and turn notices off.
  `--json --watch` prints the list again whenever it changes.
- `llmpro send TARGET TEXT` gives one message to a session that is not open, to an
  opencode session, or to a new one (`claude:new --folder DIR`), and prints the answer.
  The message goes on stdin, and no flag that allows tools or skips a permission is
  ever added (`COPILOT_ALLOW_ALL` is dropped from its environment). A hop count, a
  limit per hour and a timeout that ends the tool and everything it started keep
  sends from running away. From inside a session, a send
  to a local-only project is refused. `-n` shows what it would run. `send` is not an
  MCP tool.
- `llmpro resume TARGET` takes over a session open in another terminal on this machine,
  but only while it is idle: waiting for a reply for at least 10 seconds, its terminal
  untouched for a minute, and no child processes. It asks once (`-y` answers yes), stops
  the process, checks again, then sends SIGHUP and continues the session here. It never
  sends SIGKILL. A session on another machine is taken over by that machine's llmpro.
  `-n` shows the check and the plan.

### Changed

- The MCP `find` tool never returns local-only content: it always passes
  `--shared-only`, and a last check drops any hit under `local/` or `sessions/` (it
  says how many with `withheld`). `llmpro find` at the terminal still searches them.
- Every `llmpro sync` failure says what failed, what state the store is in, and what to
  run next. The desktop notice carries the whole message, under the app name llmpro.
- Every `llmpro setup` and `llmpro uninstall` failure says the same: what failed and
  why, what the run had changed before it stopped (and the undo script for that), and
  the command to run next. Parts setup leaves alone, such as a name clash or a plugin
  claude cannot install, are listed under "For you to do by hand".
- Tests no longer pop up desktop notices: `notify-send` is found like every other
  outside program.
- The project is now called llmpro, and so is the command. There is no `agentspro`
  command any more. Coming from agentspro: install llmpro, run `llmpro setup` (it
  rewrites the store's hooks and merge driver that still call `agentspro`), then
  `pipx uninstall agentspro`. If agentspro goes first, a commit in the store fails,
  because its hooks call a command that is gone; `llmpro setup` or `llmpro sync` fixes
  that. New undo scripts go to
  `$XDG_STATE_HOME/llmpro`; earlier ones are still found under the old name.
- The push guard's hook folder is made with mode 0755, and setup makes an existing one
  private (`chmod go-w`): gitpro refuses a `chain_to` folder that others can write.
  setup now adds the `chain_to` line itself instead of asking you to.
