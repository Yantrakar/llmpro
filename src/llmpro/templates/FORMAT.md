# Store format

The store lives at ~/.agents by default (or wherever --store or $AGENTS_STORE points) on every machine. It holds AI-assistant chats, memories, instructions, skills and notes from every machine and tool, as plain files that any assistant can read. Nothing here depends on one tool's format.

## Layout

```
AGENTS.md         entry point for any assistant
FORMAT.md         this file
memory/INDEX.md   one line per active memory: - [title](file.md) — hook
memory/<name>.md  one fact per file
history/<machine>/<tool>/<YYYY>/<YYYY-MM-DD>_<title-slug>_<id8>.md
instructions/<machine>/<tool>/...   copies of instruction files; original paths kept (home folder = HOME/)
skills/<name>/    every skill, the only copy; tools read or link to this folder.
                  skills/synced/ is Claude's copy of the claude.ai skills, git-ignored
notes/<tool>/     a tool's dated working notes, kept unchanged (not memories)
sessions/<tool>/  the tools' own raw session files, unredacted; git-ignored, never leaves the machine
config.toml       settings to tune instead of code: local-only projects, extra tool folders
local/            chats of local-only projects ([privacy] local_only in config.toml, or a repo gitpro places
                  under one of [privacy] local_identities): local/history/... and local/manifest.jsonl,
                  redacted like history/; git-ignored, never committed or sent to the hub
manifest.jsonl    one row per source item: tool, machine, kind, source_path, original_id,
                  mtime, size, output, status (imported | skipped:<reason>)
.agentspro/       allowlist.json (secret-check false positives) and memory_overrides.json
```

`<machine>` is the short hostname. `<tool>` is codex, claude-code, copilot, copilot-cli, opencode, hermes, or another plain name. History is per machine, so two machines never clash. Merge another machine's store by copying its history/ and instructions/ folders and its manifest rows, and merging memories by the rule below. Each machine's sessions/ layout is its own: on one machine it can be sessions/<tool>/<the tool's folder name>.

## Memory file

```
---
name: kebab-case-slug            # same as the filename
description: one line, used to decide relevance
type: user | feedback | project | reference
scope: global | <project name> | <machine>
status: active | superseded
superseded_by: <name>            # only if superseded
sources: [<tool>:<original id>, ...]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
The fact. For feedback and project, add **Why:** and **How to apply:** lines. Link related memories as [[name]].
```

- user: who the user is and how they work.
- feedback: how they want assistants to work (corrections and confirmed approaches).
- project: ongoing work, goals and constraints not visible in the code.
- reference: pointers to outside resources.

Scope `<machine>` is for facts about one machine (its paths, its services). INDEX.md has one section per scope.

Before adding a memory, look for one on the same thing and update it instead. When two facts conflict, the newer one wins and the older one gets `status: superseded` and `superseded_by`. Superseded files stay but drop out of INDEX.md. Never store secrets.

## Conversation file

```
---
id: <tool>:<original id>
tool: ...
tool_version: ...
machine: ...
project: <cwd or workspace folder>
title: ...
started: ISO-8601 with offset
updated: ISO-8601 with offset
source_file: <original path>
summary: |
  2-4 lines: what was asked, what was decided
---
### user · YYYY-MM-DD HH:MM
<verbatim>

### assistant · YYYY-MM-DD HH:MM
<verbatim>

> tool · shell: `<command>`
> <first 20 lines of output, then "… N more lines">
```

Kept: user and assistant messages verbatim. Each tool call becomes a one-line summary with truncated output. Each file edit becomes its path plus a diff stat, such as `edit: path (+3 -1)`.

Dropped: system and developer prompts, injected context (environment blocks, AGENTS.md/CLAUDE.md contents, system reminders, IDE selection tags), reasoning and thinking, token counts, UI and bookkeeping events, duplicate copies of a message.

## Conventions

- Heading times are the machine's local time. Frontmatter times are ISO-8601 with the UTC offset.
- A new heading starts only when the speaker changes. Tool calls always sit under an assistant heading.
- Secrets in any text are replaced with `[REDACTED:<kind>]`. The patterns are in llmpro's redact.py and lean towards removing too much.
- Sessions with no user turns are not written (manifest status `skipped:empty`). Subagent and approval-reviewer sessions are not written either (`skipped:subagent`).
- opencode keeps its sessions in one SQLite file. Its source_file is `<db path>#session/<id>`.

## Re-running

```
llmpro sync --local                 # import every tool, commit
llmpro sync                         # the same, then exchange commits with the hub
llmpro check [--staged|--history]   # secret check
llmpro summary CONV TEXT            # set a conversation's summary
llmpro summary --from FILE.jsonl    # lines: {"id": "tool:id", "summary": "..."}
```

Facts pulled out of conversations can be added by hand to a memory under a `## Added in the store` section, or as a new memory. The importer keeps that section when it re-imports a changed source memory.

Re-runs are additive and idempotent. Items are keyed by tool and original id. A conversation whose source changed is rewritten in place. Its hand-written summary is kept. If its title changed, the old file is removed. Memories keep fields edited in the store unless the source memory changed. Sources are only ever read.

## Wiring

Each tool loads AGENTS.md through one pointer outside the store, and finds its skills and logs through links:

- Codex: ~/.codex/AGENTS.md, one line pointing at the store's AGENTS.md.
- Claude Code: `@<store>/AGENTS.md` in ~/.claude/CLAUDE.md.
- Other tools: point their instruction or skills folder at the store the same way.
- Each tool's session folder (~/.claude/projects, ~/.codex/sessions, ...) may link into sessions/.
- The hub, a bare git repo reachable over ssh, is the remote that `llmpro sync` exchanges commits with.
