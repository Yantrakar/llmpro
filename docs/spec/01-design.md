# agentspro: design

Status: draft, 2026-10-16. Owner: the user. Built by: Claude subagents, phase by phase (§9).

## 1. What it is

Every AI assistant on a machine (Claude Code, Codex, Copilot, Copilot CLI, opencode, Hermes) keeps its
own chats, memories, instruction files and skills, in its own format, on that machine only. agentspro
turns them into one plain-text store that any assistant can read, keeps secrets out of it, and keeps
it the same on every machine the person uses.

Today this is ~/.agents: a git repo that holds both the data and the scripts in tools/. This spec
splits it in two, the way gitpro is split from the git config it manages:

| | agentspro (the tool) | the store (the data) |
|---|---|---|
| What | code, tests, templates, docs | history, memories, instructions, skills, notes, settings |
| Where | a Python package, installed with pipx | ~/.agents, one per person |
| Git | its own repo (this one) | a private repo; hub: a bare repo on one of the person's machines |
| Shared | can be published; holds nothing personal | never leaves the person's machines |
| Versions | semver, `agentspro --version` | `[store] format` and `redact_version` in config.toml |

### Goals

- G1. One command for each job (§3); the daily one is `agentspro sync`.
- G2. A new person, or a new machine, starts with `pipx install agentspro` and `agentspro setup`.
- G3. Nothing personal in the tool: no names, hosts, paths or project words (§8, gate T4).
- G4. Same output as today, byte for byte, until a change is made on purpose (gate T2).
- G5. Secrets stay as safe as today: the redactor, the sweep, the commit and push checks, and the
  rule that raw logs never leave the machine all carry over unchanged.

### Not goals (this round)

- Publishing to PyPI, a VS Code extension, or a hosted hub.
- New importers (Hermes; Copilot under ~/.vscode-server). They are listed in §10 and come after the split.
- Changing how redaction decides what is a secret.

## 2. Lessons taken from gitpro

- One command per job, the same name and `--json` in the terminal and over MCP (gitpro 21-redesign §7).
- A fast hot path for hooks: `agentspro hook ...` imports only what the check needs (gitpro `hot.py`).
- `setup` covers the whole machine, shows one preview and asks once; `setup -n` shows what is missing
  and exits 1 if something is.
- Each change prints how to undo it; backups and undo scripts go to the state folder.
- Tool, per-user config and per-machine state live in three separate places (§5).
- Tests never touch the real home or the network (gitpro `tests/conftest.py`).
- The version lives in one place: `src/agentspro/__init__.py`.

## 3. Jobs and commands

| Job | Command |
|---|---|
| Set up this machine, or see what is missing | `agentspro setup [-n] [-y] [--hub URL]` |
| Bring the store up to date and in step with the other machines | `agentspro sync [-n] [--local]` |
| Find something in past chats and memories | `agentspro find QUERY [filters] [--json]` |
| Check the store for secrets and broken files, fix what can be fixed | `agentspro check [--staged \| --history] [--near-miss] [--fix] [--json]` |
| Write a summary at the top of a conversation | `agentspro summary CONV TEXT` / `agentspro summary --from FILE.jsonl` |
| Take agentspro off this machine (the store is kept) | `agentspro uninstall [-n] [-y]` |
| Give assistants read-only access | `agentspro mcp` |

Called by git, hidden from help: `agentspro hook pre-commit|pre-merge-commit|pre-push`,
`agentspro merge-driver manifest %O %A %B`.

Common flags, the same in every command: `-n/--dry-run`, `-y/--yes`, `--json`, `-q`, `-v`,
`--store PATH` (default: `$AGENTS_STORE`, then ~/.agents). Results go to stdout, progress and messages to
stderr. Exit codes: 0 ok, 1 problems found, 2 error, 130 interrupted. No colour when piped or with `NO_COLOR`.

### 3.1 setup

First run on a machine. Each item is shown in one preview, grouped, then one y/N:

1. The store. If none exists: with `--hub URL`, clone it (`git clone URL ~/.agents`, which asks for the
   password once over ssh); without one, create a new store from the templates (AGENTS.md, FORMAT.md,
   config.toml, .gitignore, .gitattributes).
2. Git settings of the store: the hooks (each a one-line script in `.git/hooks/` that runs
   `agentspro hook <name>`), the manifest merge driver, `merge.ours.driver`. Today these are set by
   sync.sh on every run; setup sets them, and `sync` repairs them silently if they are missing.
3. Session folders: each tool's chat-log folder is moved into `sessions/<tool>/` with a symlink left at
   the old path (today tools/link_sessions.sh). Only for tools that are installed. Refuses while a tool is
   running, and says which.
4. Instruction pointers: each assistant's global instruction file reads the store's AGENTS.md (today
   `@~/.agents/AGENTS.md` in ~/.claude/CLAUDE.md, and the like). Shown, added only if missing.

Later runs show only what is missing. `setup -n` prints the state of each item and exits 1 if one is
missing. Undo: setup writes `~/.local/state/agentspro/setup-undo-<stamp>.sh`.

### 3.2 sync

Today's tools/sync.sh, same order and same checks: lock, repair git settings, log in to an ssh hub
once for the whole run (the ControlMaster session closed on exit; no keys are installed, the user,
2026-10-15), import, commit (the pre-commit hook checks), fetch, check what came in, merge, rebuild the
memory index, push (the pre-push hook checks). `--local` stops after the commit.

`sync -n` changes nothing and prints: sessions new or changed since the last import, commits ahead of and
behind the hub (from the last fetch; no login), and sources found on the machine that no importer reads.

Decision D1 (for the user): `sync` does not ask before it runs. Every change it makes is a git commit
in the store, undone with git, and it runs from timers without a terminal. This is an exception to
"a command that changes something asks once" and is written here as one.

Version guard: the store's config.toml carries `[store] redact_version`. The tool carries its own
REDACT_VERSION, raised whenever a change to the redactor can change its output. `sync` refuses (exit 2,
"upgrade agentspro: the store was written by a newer redactor") if the tool's is lower, and writes the
new number into config.toml, in the sync commit, if the tool's is higher. A machine with an old tool
can then not rewrite what a newer one wrote (the churn seen on 2026-10-15).

### 3.3 find

Searches history/ and memory/ (and local/history/ on this machine), case-insensitive, words ANDed.
Filters: `--machine`, `--tool`, `--since DATE`, `--memory`, `--history`. Prints one line per
conversation or memory: date, machine/tool, title, path, and the summary line; `-v` adds matching lines.
`--json` gives the same as a list. Reads only; never prints from sessions/.

### 3.4 check

Today's secretcheck.py, plus store health. Default: the working tree (`--all` today). `--staged`: what
is staged (the pre-commit hook). `--history`: every blob in history (the pre-push hook and the fetch
check). Health checks: memory/INDEX.md out of date, a memory without the required frontmatter, a
history file missing from manifest.jsonl. `--fix` rebuilds INDEX.md and nothing else; secrets are
never fixed automatically, the report says where they are (values masked). `--near-miss`: today's
near_miss.py report, exit 0.

### 3.5 summary

Today's set_summary.py. CONV is a path or `tool:id`. Summaries are kept across imports, as now.

### 3.6 uninstall

Moves session folders back and removes the symlinks, removes the store's hooks and merge drivers and
the instruction pointers it added. Never deletes the store. Preview, one y/N, undo script.

### 3.7 mcp

stdio MCP server. Tools: `find`, `check` (runs `check -n`), `sync` (runs `sync -n`). Read-only; when
something needs doing it returns the command for the person to run. Phase 4, after the split works.

## 4. What happens to each file in ~/.agents/tools

| Today | After |
|---|---|
| common.py, claude_code.py, codex.py, copilot.py, copilot_cli.py, opencode.py | `agentspro/redact.py`, `agentspro/sources/*.py` |
| import_all.py, import_history.py, import_memory.py, import_instructions.py | `agentspro/importer/*.py`, run by `sync` |
| secretcheck.py | `agentspro/check.py` (`check`, `hook`) |
| near_miss.py | `check --near-miss` |
| set_summary.py | `summary` |
| merge_manifest.py | `merge-driver manifest` |
| sync.sh | `sync`. One release: a 3-line shim in the store that prints the new command on stderr and runs it |
| link_sessions.sh | `setup` / `uninstall` |
| hooks/* | written by `setup` into `.git/hooks/` |
| test_redaction.py, test_recall.py | `tests/` |
| secretcheck_allowlist.json, memory_overrides.json | data: `.agentspro/allowlist.json`, `.agentspro/memory_overrides.json` in the store |
| merge_facts_2026-10-01.py, move_to_dot_agents.sh | dropped: one-off migrations already done; they stay in the store's git history |

Each command in §3 replaces a script; no script survives as a second way to do the same job.

## 5. Where things live

- Tool: the pipx venv; `agentspro` on PATH. Python 3.11+ (tomllib), standard library only.
- Store: `--store`, `$AGENTS_STORE`, ~/.agents. Its layout is FORMAT.md, which stays in the store so any
  assistant can read the store without the tool. The tool holds templates for a new store, nothing more.
- Store settings: config.toml in the store (as now), with a new `[store]` table:
  `format = 1`, `redact_version = N`. `[redact] never_sweep = [...]` holds words this person does not
  want swept; the tool adds the login name and host name at run time instead of hard-coding them.
- Per-clone caches: `.git/agentspro/` in the store (today .git/agents-import and .git/secretcheck), keyed
  by the tool version and REDACT_VERSION instead of a hash of common.py.
- Per-machine state: `$XDG_STATE_HOME/agentspro` (default ~/.local/state/agentspro), mode 0700: undo
  scripts, the sync log.
- Raw logs: `sessions/` in the store, git-ignored, as now. Not moved (3.5 GB, and every tool links to it).

## 6. Package layout

```
agentspro/
  pyproject.toml              hatchling, dynamic version, entry point agentspro = agentspro.cli:main
  src/agentspro/
    __init__.py               __version__, REDACT_VERSION
    cli.py                    argparse, common flags, exit codes, dispatch
    hot.py                    `hook` and `merge-driver` without importing the rest
    store.py                  locating the store, config.toml, the [store] guard
    redact.py                 today's common.py redaction and sweep
    check.py                  today's secretcheck.py
    nearmiss.py
    sources/                  claude_code, codex, copilot, copilot_cli, opencode
    importer/                 history, memory, instructions, all (today's import_*.py)
    sync.py  setup.py  find.py  summary.py  mcp.py
    templates/                AGENTS.md, FORMAT.md, config.toml, .gitignore, .gitattributes for a new store
  tests/                      test_redaction.py, test_recall.py, test_cli.py, test_golden.py, conftest.py
  docs/spec/
```

The port moves code; it does not rewrite it. Function bodies, regexes and their comments stay as they
are, so the golden test (T2) holds. Module-level paths (HOME, the store, CONFIG_FILE) become parameters
or come from store.py.

## 7. Safety rules the tool keeps

- Never prints a secret value: reports mask as today (first three characters, length, sha256 prefix).
- sessions/ and local/ never reach git; `check` fails if git ever sees a file there.
- Never adds a git remote and never pushes outside `sync`.
- Never edits skills/ by sweep (SWEPT stays history, memory, instructions).
- `setup` never reads key or credential files; it moves folders, it does not open them.

## 8. Tests and gates

- T1. tests/ passes: today's 19 redaction tests and the recall test with the same KNOWN_GAPS.
- T2. Golden: on two scratch clones of the store, today's `tools/import_all.py` followed by
  `git add -A`, and `agentspro sync --local`, give the same git tree (`git write-tree` against the
  commit's tree), apart from tools/ itself. Both read the real session folders, read-only. Chats still
  being written during the run (the session running the test, for one) may differ: every differing path
  must be a history file whose source changed after the first run started, and is listed by path only.
- T3. `agentspro check` and today's `secretcheck.py --all` give the same result on the store.
- T4. `git grep -iE` for the person's names, hosts, project words and home path finds nothing in the tool
  repo (the list is kept outside the repo, in the store's config). Test paths use /home/u.
- T5. `agentspro setup -n` on a set-up machine exits 0 and lists every item as present; it writes nothing.
- T6. Tests use a temporary HOME and a temporary store; none reads ~/.agents or the network.

## 9. Phases

Phases 1-3 touch only this repo and scratch folders. Phase 5 changes the store and every machine
and goes step by step with the user's yes.

1. Port: package skeleton, code moved, store path made a parameter, personal strings moved to config,
   `sync`, `check`, `hook`, `merge-driver`, `summary`. Gates T1-T4, T6.
2. `setup`, `uninstall`, `sync -n`, the version guard. Gate T5, tested in a temporary HOME.
3. `find`, `check --near-miss`, `check --fix`.
4. `mcp`.
5. Switch over (the user approves each step):
   a. The hub machine: `pipx install <path to this repo>`; `agentspro setup -n`.
   b. Store commit "Move the tools to agentspro": remove tools/ except the sync.sh shim, add .agentspro/,
      add `[store]` to config.toml, update AGENTS.md (commands instead of scripts) and the memories
      that name tools/*.py. `agentspro setup` writes the hooks. `agentspro sync`.
   c. Each other machine: `pipx install git+ssh://<user>@<hub machine>/<path to this repo>` (one password), `git pull hub
      master`, `agentspro setup -n`, then `setup`, then `agentspro sync`.
   d. Optional: rename the hub to ~/.agents-data.git, and its URL on every machine.
   e. The release after: remove the sync.sh shim.

## 10. Later

- Importers: Hermes (sessions/hermes), Copilot chats under ~/.vscode-server/data/User.
- `setup` for a second person: a store and hub of their own, nothing shared with the first.
- Publishing the tool (PyPI, like gitpro), once T4 has held for a while.

## 11. Decisions

The user approved all three on 2026-10-16 ("ok go ahead"):

- D1. `sync` runs without asking (§3.2).
- D2. The hub is renamed to ~/.agents-data.git at switch-over (§9 5d).
- D3. The command is `agentspro`, with no short alias (`ag` is the silver searcher).
- D4. Direction (the user, 2026-10-16): agentspro is the go-to tool for working with AI and LLMs. Its jobs
  are to share skills, memory and history, and to protect against secret and identity leaks. It takes in
  gitpro, and later other helpers (code versioning and more), behind one command line, one MCP server and
  one skill. gitpro stays installable and usable on its own ("it can remain like this. but agentpro can
  swallow it and become a goto tool, mcp, skiil, code versioning tools helper etc"). How it takes gitpro
  in is not decided yet.
