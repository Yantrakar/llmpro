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

## I5. llmpro writes one line of gitpro's config, and no other file the user keeps by hand

The push guard needs `[guard] chain_to` in gitpro's config (`$GITPRO_CONFIG`, else
`~/.config/gitpro.toml`). llmpro adds that line itself, in setup's one preview and question, after a copy
of the file as it was (`<config>.before-llmpro`), then runs `gitpro setup -y --agent none` so gitpro's
hooks pick it up; the undo script restores the copy and runs gitpro setup again. Uninstall takes out only
the line llmpro wrote. A file that is not valid TOML, a `chain_to` that is already set, or a `[guard]`
table llmpro cannot place the line in safely is left alone, and setup prints the lines to add by hand.
Every other file the user writes by hand (Claude Code's settings files among them) llmpro never writes.

Changed on 2026-10-20 at the user's word ("llmpro by default should add to gitpro dirs where its about
git and hooks"). Before, llmpro only printed the line and the user added it.

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

## Round 4 (2026-10-21): live sessions, version 1

One place to see which assistant sessions are waiting for the user, on this machine and on the
machines named in `[machines]`, and to continue them. The facts below were checked on 2026-10-21 against
Claude Code 2.1.293 and 2.1.305, Copilot CLI 1.0.100 and 1.0.101, and opencode 2.0.30, with throwaway
sessions. Packages 15 to 20 build it. Existing tools that do this (agent-deck, claude-squad, ccmanager,
Agent of Empires) need tmux, start every session themselves, or are far too large; llmpro builds its own.

## S1. One command, `llmpro resume`

`llmpro resume` with no target lists the open sessions on every machine, those waiting for the user
first. `llmpro resume TARGET` continues one session in this terminal. `--watch` keeps the list current
and sends a desktop notice when a session starts waiting. `-n` and `--json` give the list and change
nothing. A target is `[tool:]name-or-id-prefix[@machine]`; a name that matches more than one session is
an error that lists the matches. On a terminal, the list ends with one question, "Continue which?",
which takes a number or a name; Enter alone quits. There is no separate list, attach or inbox command.
Live sessions need Linux (`/proc`, pidfd); on other systems `resume` and `send` say so and exit 2.

## S2. llmpro reads each tool's own records and writes nothing into them

| Tool | Open sessions | State |
|---|---|---|
| Claude Code | `~/.claude/sessions/<pid>.json` (`sessionId`, `cwd`, `name`, `status`, `pid`, `procStart`, `version`) | `status`: `idle`, `busy`, `waiting` |
| Copilot CLI | `~/.copilot/session-state/<id>/inuse.<pid>.hold`, on which a live `copilot` process keeps a `flock`, and a `copilot` process whose command line names `--resume <id>` (a resumed session takes its hold file only after its first prompt) | the last known event type in `events.jsonl` |
| opencode | its server, through `opencode api` (`session.active`, the sessions of live `opencode` clients); the server owns each session and clients share it | `session.active` and `session.permission.list` |
| Codex | not listed in version 1: on these machines it runs only inside the VS Code extension | |

Each tool has one adapter. It states the tool versions it was checked against. A record whose keys are
missing or of the wrong type gives the row `not-readable`, never a guess. llmpro never opens
`~/.claude/sessions/*.key`, never uses `messagingSocketPath`, and never reads another process's
environment. A pid is trusted only when `/proc/<pid>` exists, belongs to the user, runs the tool, and
(for a record that has `procStart`) started when the record says.

## S3. Shared states

Every row has one of these states, whatever the tool calls it:

| State | Meaning | Claude Code | Copilot CLI |
|---|---|---|---|
| `needs-approval` | a permission prompt is up | `waiting` | `permission.requested` not yet followed by `permission.completed` |
| `needs-reply` | the turn is over; it waits for the next message | `idle` | `assistant.turn_end`, `abort`, `session.error`, `session.resume` or `session.start`, then only bookkeeping events (usage, info, model and permission changes, notifications, `model.*`; the full list is in 15) |
| `working` | it is busy | `busy` | a known event of work (messages, tools, subagents, skills, compaction) |
| `unknown` | open, but the state cannot be told | | an unknown last event type, `session.shutdown`, or none |
| `not-readable` | the record is in a form this llmpro does not know | | |

Rows are sorted `needs-approval`, `needs-reply`, `working`, `unknown`, `not-readable`, then by time in
that state, longest first.

## S4. Content stays out of the list where it must

A row shows the tool, machine, name, folder, state and time in that state. For `needs-approval` and
`needs-reply` it may also show one line: the permission's own summary or the start of the last
assistant message, redacted (`redact.redact`) and with control characters removed. A session whose
folder is local-only (`store.is_local_project`) shows its state and no line, and its transcript is not
opened. A remote machine is asked with `--no-line` and sends its rows without the line. The MCP server
gets the `sessions` tool, read-only; its gate removes every line and every local-only row itself, as
`gate_find` does (I4, H2). A desktop notice names the tool, the session and the machine, and for a
local-only session only the tool and the machine (notices can reach a phone). Every string llmpro prints
from a record (name, folder, line, anything from another machine) has its control characters removed.
Nothing about live sessions is written to the store.

## S5. Machines are named in config.toml and reached with plain ssh

`[machines]` in the store's `config.toml` maps a name to an ssh target. The entry for this machine is
skipped. An entry that is not well formed (a target starting with `-`, a character outside
`[A-Za-z0-9._@-]`) is an error row for that machine, never an exit, so it cannot break `sync` or `find`.
llmpro runs `ssh -T -o BatchMode=yes -o ConnectTimeout=5 -- <target> <llmpro> resume --local --json
--no-line` on each machine at once and waits at most 10 seconds. The remote command is built only from
words quoted with `shlex.quote`; a path under the home folder is sent as `"$HOME"/` followed by the quoted
rest. The asking side sets each remote row's `machine` to the `[machines]` name. A machine that cannot be asked is one row
saying why, never a failure of the whole list. The remote answer must carry
`"format": "llmpro-sessions.v1"`; any other format is a row naming `pipx upgrade llmpro`. llmpro never
asks for an ssh password in a list: a machine that needs one says so. `--watch` opens one shared ssh
login per machine at its start (ssh ControlMaster, as `sync.Hub` does) and closes it when it ends.
Continuing a remote session runs `ssh -t -- <target> <llmpro> resume TARGET`: everything after that, a
takeover included, happens on that machine.

## S6. llmpro starts tools as they would start by hand, and leaves the host as it was

To continue or ask a session llmpro runs the tool's own resume command in the session's folder, as a
child in the same terminal, and returns when it exits. It removes the variables that tell a tool it
runs inside another (`CLAUDECODE`, `CLAUDE_CODE_*`, `CLAUDE_PID`, `CLAUDE_EFFORT`, `AI_AGENT`, `COPILOT_*`,
`OPENCODE`, `OPENCODE_*`, `VSCODE_*`, `*_VSCODE_SNAP_ORIG`, `LLMPRO_*` except the hop count, and the
variables a tool sets only for its own shell commands, listed in 17), removes `TERM_PROGRAM` and `TERM_PROGRAM_VERSION` only when
`TERM_PROGRAM` is `vscode`, and keeps everything else (PATH, LANG, SSH_AUTH_SOCK, proxies, keys). Sessions llmpro starts must not change host settings (the user,
2026-10-21). Copilot CLI 1.0.100 once wrote a Shift+Enter binding into VS Code's `keybindings.json` on
its first start: its terminal setup, offered once in a VS Code terminal (facts in 17). So llmpro hashes a short list of editor settings files before
it starts a tool and after the tool exits. If one changed while the tool ran, it says so, shows the
`diff` against the copy the tool kept, and how to put it back; it does not put it back itself (rule 7),
because the editor or the user may have changed the file too.

## S7. Taking over a session open elsewhere, only while idle

When the target is open in another terminal, llmpro closes it there and continues it here, only when it
is `needs-reply` and has been for at least 10 seconds, its terminal has had no keystroke for at least 60
seconds, it has no child processes, and no other llmpro is taking it (a lock per session). It shows what
it will close and asks once. It holds the process through a pidfd, stops it, checks again, then sends
SIGHUP and SIGCONT. It never sends SIGKILL. A session in `needs-approval` or `working` is never taken.
There is no undo for a closed process, so llmpro prints the command that continues the session again
instead.

## S8. One session can ask another, with limits

`llmpro send TARGET TEXT` gives one message to a session and prints its answer. It works only where
there is a channel that cannot collide with a person typing: a session that is not open (the tool's own
headless resume), an opencode session (its server), and a new session (`tool:new`). A session open in a
terminal is refused with where it is open. Sends carry a hop count (`LLMPRO_SEND_HOPS`); a send from
inside a chain longer than `[sessions] max_hops` (default 2) is refused, and one session takes at most
`[sessions] sends_per_hour` (default 20) sends. The hop count guards against loops made by mistake; it
is not a security boundary. llmpro never passes a flag that widens what the tool may do without asking,
so a headless turn that needs a permission is denied by the tool. The message never becomes a flag: it
goes to the tool on stdin, or after `--`. Agents never answer permission prompts. A send to a local-only
session, or a new session in a local-only folder, from inside another session is refused, on this
machine and on any other; the user at a terminal may still send one. Like the hop count, this check stops
mistakes, not a determined caller. `send` is a
command, not an MCP tool: the MCP server stays read-only (I4).

## S11. One llmpro at a time per session

A resume, a send and a takeover each hold a lock for the session
(`$XDG_STATE_HOME/llmpro/live/<tool>-<id>.lock`, `fcntl.flock`) for their whole run. After taking it,
llmpro reads that one session again and refuses if it is now open, so a choice made from a list shown
minutes earlier cannot start a second writer. Ids are checked against `[A-Za-z0-9-]+` before they name a
file.

## S9. Hosting, key bindings and approvals wait for version 2

Version 1 runs tools in the user's terminal and holds no pty of its own. Not in version 1: `llmpro run`
and a detached host that survives a dropped ssh, one set of key bindings inside llmpro (none are written
to the host, ever), approving permission prompts from the list, typing into an open session, Claude
Code's peer channel, and Codex. The takeover (S7) stays in version 1, as its last package and one that
can be left out, because the sessions the user wants to reach are already open in editor panes;
`llmpro run` would help only sessions started after it exists.

## S10. Facts to check before building

The integrator checks these with throwaway sessions before the wave that needs them, and records the
answer in that package file. All ten were checked on 2026-10-21; the answers changed 15 (the Copilot hold
file, Claude sessions in containers, the opencode operations), 17 (the variables removed), 19 (the
channels and their JSON) and 20 (container and opencode rows are never taken over):
1. (15) The Copilot lock file: a real lock in `/proc/locks`, or a name only.
2. (15) opencode 2.0.30: the exact `opencode` command line that returns `session.active` and
   `permission.request.list` as JSON, whether its server needs a password, and whether `opencode attach`
   lets two clients share a session (then opencode needs no takeover).
3. (17) Claude Code: `--resume` keeps the session id and `-n` name; what a half-typed draft becomes on
   SIGHUP (Copilot as well).
4. (17) What made Copilot write `keybindings.json` (`TERM_PROGRAM`, a missing config, or its first
   start), and the list of settings files S6 hashes.
5. (19) The headless forms: `claude -p --resume <id>`, `copilot -p --resume <id>` and opencode's, with
   `--model`; what each records as the model really used.
6. (20) Whether logind kills a user's processes at logout on each machine (`KillUserProcesses`).
7. (15) The unit of Claude's `procStart` against the start time in `/proc/<pid>/stat`; the program name
   of a Copilot CLI process (`copilot` or `node`).
8. (17) Where a closed Claude session's folder can be read (the transcript's `cwd`).
9. (19) Which tools read the prompt from stdin, and which print the session id in a JSON output; what
   Copilot records as `selectedModel` after a model change.
10. (19) The variables each tool (Claude Code, Copilot CLI, opencode, Codex) sets for the shell commands
    it runs, so llmpro can tell a call from inside a session (`launch.INSIDE`).
