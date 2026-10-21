# llmpro

llmpro keeps what your AI assistants know in one place, and keeps secrets out of it.

Claude Code, Codex, Copilot, Copilot CLI and opencode each keep their own chats, memories, instruction files and skills. llmpro collects them into one plain-text git repo, the store (default `~/.agents`). Every assistant can read it. Secrets are redacted on the way in and checked before every commit and push.

## Install

You need Python 3.11 or later and git.

    pipx install llmpro

For the git identity features, install gitpro too. Either of these works:

    pipx install "llmpro[git]"
    pipx install gitpro

## Set up

First look at what would change. This changes nothing.

    llmpro setup -n

Then apply it. llmpro shows the same list, asks once, and writes an undo script.

    llmpro setup

Setup creates the store, moves each assistant's chat folders into it (a link stays behind), points each assistant's instruction file at the store, installs the secret-check hooks, registers the llmpro MCP server with Claude Code and Codex, and puts the llmpro skill in the store. To import everything now:

    llmpro sync --local

## Commands

- `llmpro sync` imports chats, memories and instructions, commits, and exchanges commits with your hub. `--local` leaves the hub out. `-n` shows what a sync would do.
- `llmpro find WORDS...` searches past chats and memories.
- `llmpro check` looks for secrets in the store. `--staged` and `--history` change what it looks at. `--fix` rebuilds the memory index.
- `llmpro summary` sets the summary at the top of a chat.
- `llmpro setup` sets up this machine. `-n` only shows. `-y` does not ask.
- `llmpro uninstall` takes llmpro off this machine. It keeps the store. `-n` only shows.
- `llmpro resume` lists the open assistant sessions. `llmpro resume TARGET` continues one. `llmpro resume --watch` keeps the list current. See Live sessions.
- `llmpro send TARGET TEXT` gives one message to a session and prints the answer. See Live sessions.
- `llmpro git ...` runs gitpro, if it is installed.
- `llmpro mcp` serves llmpro's tools to assistants over stdio.

Every command accepts `--store PATH`, `-q` and `-v`. Many accept `--json`. Exit codes: 0 done, 1 a check or step failed or something is missing, 2 usage or other error.

## For your assistants

llmpro gives assistants three things.

- The store's `AGENTS.md`, which tells them to read the memory index first.
- The skill `skills/llmpro`, copied into the store by `llmpro setup`. Assistants that read `skills/` from the store get it with no more work.
- The MCP server, with the tools `find`, `check` and `sync_status`. When gitpro is installed it adds gitpro's tools with a `git_` prefix. All of them are read-only.

### Claude Code plugin

The plugin brings the skill, the MCP server and a hook in one step.

    /plugin marketplace add Yantrakar/llmpro
    /plugin install llmpro@llmpro

The hook runs before each Bash call. If gitpro is installed and can check commands, llmpro hands the call to `gitpro agent-check`. Otherwise it does nothing and never blocks. If gitpro's own plugin is enabled too, the check runs twice. Keep one of them.

`llmpro setup` also registers the MCP server with Claude Code. With the plugin installed, Claude Code then starts it twice. Keep the plugin's and run `claude mcp remove --scope user llmpro`.

## Live sessions

Linux only. llmpro reads the records that Claude Code, Copilot CLI and opencode keep of their sessions. It never writes into them.

- `llmpro resume` lists the open sessions here and on the machines in `[machines]` of `config.toml`, each with its state: `needs-approval`, `needs-reply`, `working` or `unknown`. `--local` asks this machine only. `--json` prints the rows.
- `llmpro resume TARGET` continues a session in your terminal. TARGET is `[tool:]name-or-id-prefix[@machine]`. A session that is open in another terminal on this machine is taken over only while it is idle: `needs-reply` for at least 10 seconds, its terminal untouched for 60 seconds, and no child processes. llmpro shows what it will close, asks once (`-y` answers yes), stops the process, checks again, sends it SIGHUP (never SIGKILL) and continues the session here. Anything typed there and not sent is lost, and llmpro prints the command that continues the session again. A session on another machine is taken over by that machine's llmpro, over ssh. `-n` shows the check and the plan and changes nothing.
- `llmpro resume --watch` keeps the list current and sends a desktop notice when a session starts waiting. `[sessions] refresh` and `notify` in `config.toml` tune it.
- `llmpro send TARGET TEXT` gives one message to a session that is not open, to an opencode session, or to a new session (`claude:new`, `copilot:new`, `opencode:new`, with `--folder DIR`). It prints the answer. `TEXT` of `-` reads stdin. `-n` shows the command and runs nothing.

Limits on `send`:

- The message goes to the tool on stdin, never on the command line.
- llmpro never adds a flag that allows tools or skips a permission. A turn that needs a permission is refused by the tool.
- It never types into a session that is open in a terminal.
- A message carries a hop count. `[sessions] max_hops` (default 2) stops loops made by mistake. `sends_per_hour` (default 20) limits one session. `send_timeout` (default 900 seconds) ends a send that takes too long.
- From inside a session, a send to a local-only project is refused. This stops mistakes. It is not a security boundary.
- `send` is not an MCP tool. The MCP server stays read-only.

## Push guard

The push guard checks what a push adds, in any repo, for secrets. If it finds one, the push stops.

It works through gitpro's global git hooks. `llmpro setup` adds this to `~/.config/gitpro.toml`, in the same preview and question as the rest, and keeps the file as it was in `~/.config/gitpro.toml.before-llmpro`:

    [guard]
    chain_to = "~/.local/share/llmpro/hooks"

Then it runs `gitpro setup -y --agent none`, so gitpro's hooks run the push guard. The undo script puts the file back. `llmpro uninstall` takes the line out again. If the file is one llmpro cannot edit safely (for example `chain_to` is already set), setup leaves it and prints the line to add.

To let one push through, run `LLMPRO_SKIP=1 git push`.

## Keep work chats on this machine

Folders listed in `local_only` keep their chats out of the git history that you sync. With gitpro installed you can do it by git identity instead. In the store's `config.toml`:

    [privacy]
    local_only = ["~/code/private"]
    local_identities = ["work"]

Chats of any project that gitpro places under the `work` identity go to `local/`, which git ignores. If llmpro cannot tell which identity a folder belongs to, its chats stay local. If gitpro is missing while `local_identities` is set, `sync` stops and says why.

## How gitpro fits

gitpro is a separate tool that picks the git name, email, SSH key and commit signing for each repo by its remote. It has its own releases. llmpro never imports it: it finds the `gitpro` program, asks what it can do, and runs its commands. If gitpro is missing or older than 0.4, the git features are hidden and the rest works. See https://github.com/Yantrakar/gitpro.

When gitpro has setup work to do, `llmpro setup` lists it in the same preview and runs `gitpro setup -y --agent none` after your yes. The option `--agent none` stops gitpro from registering a second MCP server and hook.

## Uninstall

    llmpro uninstall -n
    llmpro uninstall

This takes back what setup did: the hooks, the registrations and the moved folders. The store is never deleted. Every setup and uninstall writes an undo script, and prints its path.

## Development

    uv venv .venv && uv pip install -e '.[test]'
    .venv/bin/pytest -q

Tests run offline, in a temporary home folder, with a fake gitpro. See `docs/releasing.md` for how a release is made.
