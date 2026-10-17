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

## Push guard

The push guard checks what a push adds, in any repo, for secrets. If it finds one, the push stops.

It works through gitpro's global git hooks. llmpro never writes your gitpro config, so after `llmpro setup` you add this to `~/.config/gitpro.toml` yourself:

    [guard]
    chain_to = "~/.local/share/llmpro/hooks"

`llmpro setup` prints the line when it is missing. To let one push through, run `LLMPRO_SKIP=1 git push`.

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
