---
name: llmpro
description: Use llmpro when the user asks about past chats or what was decided before, wants something remembered across sessions, is about to commit or push, asks which git name, email or key a repo uses, or worries about secrets in a repo or in the assistant context store. llmpro keeps every assistant's chats, memories, instructions and skills in one git store (usually ~/.agents), redacts secrets, checks the store and every push for secrets, and, when gitpro is installed, answers git identity questions through git_* tools.
license: MIT
---

# llmpro

llmpro keeps the chats, memories, instruction files and skills of every AI assistant on a machine in one plain-text git repo, the store. The store is usually `~/.agents`. Secrets are redacted on the way in and checked before every commit and push.

## The store

- `memory/INDEX.md` lists every active memory. `memory/<name>.md` holds one fact each.
- `history/<machine>/<tool>/<year>/` holds past chats, one file each, with a short summary at the top.
- `instructions/` holds copies of instruction files. `skills/<name>/SKILL.md` holds the skills.
- `local/` holds chats that must stay on this machine. Git ignores it.
- `sessions/` holds raw logs that may contain secrets. Git ignores it. Never read or print it.
- `FORMAT.md` in the store describes every file format.

## Rules

- Never push the store. The user runs `llmpro sync`, which is the only thing that pushes it.
- Never add a git remote to the store.
- Never store a secret: no password, key, token or credential, not even where one is kept. Write where it lives, never the value.
- Run `llmpro check` before a commit in the store. Fix what it reports. Do not allowlist a hit without the user's yes.
- Never print a secret value, a long token or the content of a raw session file.
- Run `llmpro setup -n` before any `setup`. It only shows what would change.
- Never run `llmpro setup -y` or `llmpro uninstall -y` unless the user said yes to it in this conversation.
- Edit the TOML or the store's files only as these notes say. Do not edit `history/`: it is generated.

## Commands

- `llmpro find WORDS...` searches past chats and memories. Add `--memory`, `--history`, `--tool T`, `--since YYYY-MM-DD` or `--json`.
- `llmpro check` runs both secret checks. `--staged` checks what is staged. `--fix` rebuilds `memory/INDEX.md`.
- `llmpro sync --local` imports and commits on this machine only. `llmpro sync -n` shows what a sync would do.
- `llmpro summary` sets the summary at the top of a chat.
- `llmpro setup -n` shows what is missing on this machine. `llmpro uninstall -n` shows what would be removed.
- `llmpro resume` lists the open assistant sessions (`--json` for rows). `llmpro resume TARGET` continues one in the user's terminal; only the user starts that. `llmpro resume --watch` keeps the list current.
- `llmpro send TARGET TEXT` gives one message to a session that is not open, to an opencode session, or to a new one (`claude:new --folder DIR`), and prints the answer. `-n` shows what it would run.
- `llmpro git ARGS...` runs gitpro when it is installed (see the git reference).
- `llmpro mcp` serves the tools below over stdio. The Claude Code plugin registers it.

## Tools (MCP)

- `find`, `check` and `sync_status` are llmpro's own.
- `git_*` tools appear when gitpro is installed, for example `git_why` for the identity a repo uses.
- All of them are read-only.

## When to use what

- The user says "we did this before" or "what did we decide": run `llmpro find`, then open the best match. See [memory](references/memory.md).
- The user says "remember this": add a memory. See [memory](references/memory.md).
- You learned a lesson (what worked or failed, and the chats that show it): see Lessons in [memory](references/memory.md).
- Before you commit in the store, or when the user asks about secrets: see [secrets](references/secrets.md).
- Before you commit or push in any repo, or when the user asks which identity a repo uses: see [git](references/git.md).

## Sending to another session

- Use `llmpro send` when the user asks you to hand work or a question to another session, or to start a new one in another folder. Give a self-contained message: the other session does not see this chat.
- Never use `send` to ask for an approval, a permission or a yes from another assistant. Approvals are the user's.
- Do not send to a session that is open in a terminal. llmpro refuses it. Do not retry in a loop: a message carries a hop count and a limit per hour.
- Do not try to get around a refusal, for example for a local-only project. Tell the user and let them send it from their own terminal.
- Read the answer on stdout. A warning on stderr about the model means the tool used another model than asked.

## If llmpro is not found

Tell the user to run `pipx install llmpro`, then `llmpro setup -n`. Do not install it yourself.
