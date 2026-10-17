# Assistant context store

This folder is the assistant context store, kept as plain files so any assistant can read it: past chats, memories, instruction files and skills from the AI coding tools used on every machine that syncs here. No tool owns it. Every tool points here, nothing else holds an original. It is a local git repo, kept by `llmpro`. FORMAT.md describes the layout and the file formats.

## At the start of a session

1. Read memory/INDEX.md. It lists every active memory, grouped by scope: global first, then one section per project, and one per machine.
2. Open the global memories that bear on the task, the ones for the project you are working in, and the ones in the section named after this machine (`hostname -s`).
3. A memory is true as of its `updated` date. If two disagree, the newer wins. If one contradicts what you see now, trust what you see and fix the memory.

## Looking things up

- Past conversations: history/<machine>/<tool>/<year>/<date>_<title>_<id>.md, one per file, each starting with a short summary. Grep before asking the user again, for example `grep -ril "ansible" history/`.
- instructions/<machine>/<tool>/ holds copies of instruction files at their original paths, with the home folder as HOME/. The live original is at the source_path that manifest.jsonl gives.
- skills/<name>/SKILL.md: every skill, and the only copy of each. Edit them here. A skill copied anywhere else is garbage: remove it rather than keep it in step. Tools that read skills from a folder of their own should link it to the store's skills/.

## Adding or changing a memory

Add a memory when you learn something that should outlast this session: a preference or correction from the user, a project decision, or a pointer to an outside resource.

- One fact per file: memory/<name>.md, in the format given in FORMAT.md.
- First look for a memory on the same topic and update it rather than adding a second one.
- Keep the scope as narrow as the evidence: one remark stays with its project, or with its machine if it is about that machine. A memory goes global only when it repeats or the user states it as a general rule.
- If the new fact replaces an old one, set `status: superseded` and `superseded_by: <new name>` on the old file.
- Feedback and project memories carry **Why:** and **How to apply:** lines. Prefer what the user said over what you infer.
- Then rebuild the index: `llmpro sync --local`.
- Use absolute dates.

## Never store secrets

No passwords, API keys, tokens, private keys or credentials, not even inside a memory about where one lives. Write where it lives, never the value; in copied text write `[REDACTED:<kind>]`.

Before any commit, `llmpro check` must pass. It runs two checks: a pattern scan of the files, and a leak check against the secret values in the tools' raw logs. The pre-commit and pre-merge-commit hooks run it too. A false positive goes into .agentspro/allowlist.json by fingerprint, with a reason.

Never add a git remote and never push. The one remote, hub, is pushed only by `llmpro sync`, which the user runs. Assistants may commit: one commit per coherent change, with a short message.

## Raw sessions

sessions/<tool>/ holds each tool's own session files. The tools' own folders link here, so they write as before. These files are unredacted and can hold secrets: git ignores sessions/, `llmpro check` fails if git ever sees a file there, and they never leave this machine. Never run `git clean -X` here: it deletes them. history/ is their redacted, readable form.

Chats from a local-only project (`[privacy] local_only` in config.toml) are imported into local/history/ instead, with their rows in local/manifest.jsonl. git ignores local/, `llmpro check` fails if git ever sees a file there, and they never reach the hub. To make another project local-only, add its folder there and move its existing chats into local/ by hand.

config.toml holds the settings meant to be changed (local-only projects, extra tool folders to read). Edit it rather than the code, then run `llmpro check`.

## History is generated

Don't edit history/ by hand. `llmpro sync --local` rebuilds it from each tool's own files, which it only ever reads, and refreshes memories and instruction copies. `llmpro sync` runs the import and commits. The summaries at the top of each conversation are the exception: they are written by hand (`llmpro summary`) and kept across runs.
