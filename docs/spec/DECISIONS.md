# Decisions for taking in gitpro

Proposed 2026-10-17 by Claude while the user was away ("propose ideas ... use standards and some clever
ways", "dont wait for me now"). Each is open to the user's change; until then the packages follow them.
D1 to D4 are in `01-design.md`; D4 there says llmpro "takes in gitpro ... behind one command line, one MCP
server and one skill; gitpro stays installable on its own".

## I1. Two repos, two packages; gitpro is found, not imported

llmpro never imports gitpro. It finds the `gitpro` program (`$LLMPRO_GITPRO`, else `gitpro` on PATH),
reads `gitpro --version --json`, and runs gitpro commands with `--json`, the way gitpro's VS Code
extension does. A missing or too old gitpro hides the git features; it never breaks llmpro.
`pip install "llmpro[git]"` adds gitpro (`gitpro>=0.4,<0.6`) for those who want one install.

Why: gitpro is public and released on its own; agentspro is private. Importing would tie the release
cycles and test suites together. gitpro's command line and its `--json` output are already a contract
that another program (the VS Code extension) relies on.

## I2. Plugins through Python entry points

Group `llmpro.plugins`; contract name `llmpro-plugin.v1` (03). The three built-in plugins (`memory`,
`secrets`, `git`) register the same way an outside package would.

## I3. Rename to llmpro, no agentspro command

Distribution, import package and command become `llmpro`. Changed on 2026-10-17 by the user: the
`agentspro` command is retired, not kept as an alias. Hooks and merge drivers that still say `agentspro`
count as missing, so `llmpro setup` and `llmpro sync` rewrite them; `llmpro uninstall` still removes
them. (Package 02 built the alias; it was taken out after wave 4.) The store's `.agentspro/` folder, the `.git/agentspro/` cache and
`$AGENTS_STORE` keep their names. The undo scripts move to `$XDG_STATE_HOME/llmpro/`; the old
`$XDG_STATE_HOME/agentspro/` is still read where llmpro looks for earlier undo scripts.

## I4. One MCP server, read-only, git tools proxied

`llmpro mcp` serves llmpro's own tools and, when gitpro is there, the current gitpro tools under a
`git_` prefix, through a `gitpro mcp` child started without `--allow-execution`. Tools whose description
starts with "Deprecated" are not listed.

## I5. llmpro never writes gitpro's config

The push guard needs `[guard] chain_to` in `~/.config/gitpro.toml`. llmpro prints the line; the user adds
it. This keeps gitpro's rule "suggest, user picks".

## I6. Privacy by identity keeps a chat local when in doubt

With `[privacy] local_identities` set, a chat whose folder llmpro cannot place stays local. If gitpro is
missing or too old while `local_identities` is set, `sync` stops and says why, rather than send every
chat to `local/`.

## I7. llmpro setup runs gitpro setup with `--agent none`

When gitpro has work to do, the preview shows it and, after the one yes, llmpro runs
`gitpro setup -y --agent none`. `--agent none` because llmpro's own plugin registers the MCP server and
the agent hook; gitpro registering its own as well would run the hook twice.

## I8. One Claude Code plugin from the llmpro repo

The llmpro repo carries `.claude-plugin/plugin.json`, `.mcp.json`, `hooks/hooks.json` and
`skills/llmpro/`. The hook runs `llmpro hook agent-check`, which forwards to `gitpro agent-check` when
gitpro has that capability, and passes otherwise.
