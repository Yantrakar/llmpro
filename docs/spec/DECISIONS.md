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

## Round 2 (2026-10-20): lessons from harness2

Ideas taken from Google Research's harness2 (github.com/google-research/harness2, Apache 2.0). No code
or text was copied; the credit sits in FORMAT.md. Decided by Claude while the user was away ("dont wait
for me"). The user reviewed them one by one on 2026-10-20 and kept all four; H4 was widened (below).

## H1. A lesson carries cue, evidence and confidence

Feedback and project memories may carry `cue`, `evidence` and `confidence`. At most one lesson per
session, often none, and "confounded" rather than a guess. `check` fails a lesson whose evidence is
missing or points into `local/` or `sessions/`; a memory without the fields is fine, so imported tool
memories never block a sync.

## H2. The MCP server never returns local content

The MCP `find` always passes `--shared-only`, and a last gate in `mcp.py` drops any hit under `local/`
or `sessions/` or outside the store. An assistant can be backed by a cloud model, so what the MCP server
returns counts as leaving the machine. `llmpro find` at the terminal still searches `local/history/`.

## H3. Each chat records its harness as a store commit

`harness: <12-char commit>` in a chat's frontmatter: the last first-parent commit of the store at or
before the chat started. Set once and kept. The first sync after the change rewrites every chat whose
source is on the machine, once. A content hash of the harness parts (as harness2 does) was not chosen:
the store is already a git repo, and a commit names the same thing.

## H4. config.toml [harness] names the parts; setup only reports for now

Parts: instructions, memory, skills, and optional guardrails, subagents, scripts, hooks. `setup -n`
shows which tools get each part. Placing new parts in tool configs waits for a later round.

Widened by the user on 2026-10-20 ("keep it. and include optional too. place the missing parts too"):
the template names the optional parts as well, and setup places what is missing. See P1 to P4.

## Round 3 (2026-10-20): setup places the harness

The user asked for it on 2026-10-20 (H4 above). Decided by Claude from the tools' documentation, checked
on 2026-10-20; the user reviews them.

## P1. Every part is named, and its folder exists

The config.toml template, and a store without `[harness]`, name all seven parts: `instructions =
"AGENTS.md"`, `memory = "memory/"`, `skills = "skills/"`, `subagents = "agents/"`, `hooks = "hooks/"`,
`guardrails = "guardrails/"`, `scripts = "scripts/"`. setup creates a missing part folder with a
README.md at its top saying what goes in it and how it reaches each tool.

## P2. A part reaches a tool the way that tool already loads it

Only places the tool documents. Checked 2026-10-20:

| Part | claude-code | codex | copilot-cli | opencode |
|---|---|---|---|---|
| skills | `~/.claude/skills` | reads `~/.agents/skills` only | `~/.copilot/skills`, or reads `~/.agents/skills` | `~/.config/opencode/skills`, or reads `~/.agents/skills` |
| subagents | `~/.claude/agents/*.md` | `~/.codex/agents/*.toml` | `~/.copilot/agents/*.agent.md` | `~/.config/opencode/agents/*.md` |
| hooks | a plugin (P3) | `~/.codex/hooks.json` | `~/.copilot/hooks/*.json` | `~/.config/opencode/plugins/` (JS/TS) |
| guardrails | a second plugin (P3) | not supported | not supported | not supported |

Sources: code.claude.com/docs/en/sub-agents, /plugins/components, /plugins/loading;
developers.openai.com/codex/subagents, learn.chatgpt.com/docs/hooks and /docs/build-skills;
docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference and
/reference/hooks-reference; opencode.ai/docs/agents, /docs/plugins, /docs/skills.

A folder or file is placed by a symlink into the store. If the tool's own folder already holds files,
setup moves them into the store first (as it does for session folders). A name that exists on both sides
is a clash: setup leaves that tool unplaced and says which file to merge by hand. A link that points
somewhere else is left alone and reported. `~/.codex/skills` holds only Codex's own `.system/` and is not
touched. Hermes is not placed: it is not installed on either machine and documents no subagent files.

Guardrails are hooks that stop an assistant. Codex, Copilot CLI and opencode each load hooks from one
place, so a guardrail for them goes in `hooks/<tool>/`; their guardrails line says so. llmpro's own guards
(the secret check, the push guard, the agent hook) are setup items of their own and are not repeated.

## P3. Subagents and hooks are kept per tool, in each tool's format

The tools share no subagent or hook format and llmpro converts none. The store keeps `agents/<tool>/`,
`hooks/<tool>/` and `guardrails/claude-code/`; each tool's place links to its own. Claude Code's hooks live
in its settings files, which llmpro never writes (I5). They are placed through a local marketplace the store
holds, `.claude-plugin/marketplace.json` named `llmpro-harness`, with the plugins `harness-hooks`
(`hooks/claude-code/`) and `harness-guardrails` (`guardrails/claude-code/`). setup adds and installs them with
Claude Code's own `claude plugin` commands after the one yes. A local marketplace is loaded in place, so
later edits apply at the next session without a reinstall.

## P4. Scripts go on PATH

`scripts/` holds commands for every assistant and for the user. setup links each executable file at its top
into `~/.local/bin`. A name already there that is not our link is a clash and is reported; a file that is not
executable is listed with the `chmod +x` that would place it.

## P5. Uninstall keeps the tools working

`llmpro uninstall` replaces each link setup made with a copy of what it pointed to, so a tool keeps the
agents and hooks it had, and uninstalls the two Claude Code plugins and the marketplace. The store keeps
its files.
