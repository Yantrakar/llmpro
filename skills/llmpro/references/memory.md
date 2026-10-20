# Memory and past chats

## Finding past chats

1. Run `llmpro find WORDS...`. All words must occur in the same file. Narrow it with `--tool claude-code`, `--since 2026-01-01`, `--history` or `--memory`.
2. In an assistant with the llmpro MCP server, call the `find` tool with the same words.
3. Without the command, search the files: `grep -ril "word" ~/.agents/history ~/.agents/memory`.
4. Open the best match. Each chat starts with a short summary. Check what you read against the code or the machine before you rely on it.

Chats of local-only projects are in `local/history/`, not `history/`. `llmpro find` looks there too.

## Adding a memory

Add one when you learn something that should outlast the session: a preference or correction from the user, a project decision, or a pointer to an outside resource.

1. Look in `memory/INDEX.md` for a memory on the same topic. Update it rather than add a second one.
2. Otherwise write `memory/<name>.md`, one fact per file, in the format that the store's `FORMAT.md` gives.
3. Keep the scope as narrow as the evidence. One remark stays with its project, or with its machine. It goes global only when it repeats or the user states it as a general rule.
4. If the new fact replaces an old one, set `status: superseded` and `superseded_by: <new name>` on the old file.
5. Feedback and project memories carry **Why:** and **How to apply:** lines. Prefer what the user said over what you infer.
6. Use absolute dates.
7. Never put a secret in a memory.

## Lessons

A feedback or project memory that carries `cue` and `evidence` is a lesson.

- `cue`: one line, the situation in which it applies.
- `evidence`: the chats that show it, as a path under `history/` or a source id `<tool>:<id>`. Never a path under `local/` or `sessions/`; use the source id.
- `confidence`: `high` when the user said it or it held in two or more chats, `medium` for one clear chat, `low` otherwise.
- Write at most one new lesson per session, often none. If two things changed at once and you cannot tell which one helped, write that it is confounded, or write nothing. Do not guess.
- `llmpro check` fails a lesson that cites a chat that is not there.

Example:

```
---
name: run-tests-before-commit
description: Run the test suite before every commit in the demo project
type: feedback
scope: demo
status: active
sources: [claude-code:demo/run-tests]
cue: about to commit in the demo project
evidence: [history/laptop/claude-code/2026/2026-03-02_fix-build_ab12cd34.md]
confidence: medium
created: 2026-03-02
updated: 2026-03-02
---
Run the tests before committing.

**Why:** a commit on 2026-03-02 broke the build.
**How to apply:** run the suite first; commit only when it passes.
```

## The index

`memory/INDEX.md` is generated. After a change, rebuild it with `llmpro check --fix`. Do not edit it by hand.

## Trust

A memory is true as of its `updated` date. If two disagree, the newer one wins. If one contradicts what you see now, trust what you see and fix the memory.
