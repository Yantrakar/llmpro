# llmpro specification: taking in gitpro

This folder specifies how agentspro becomes llmpro and takes in gitpro as a plugin, without merging
gitpro's code. `01-design.md` is the design of agentspro itself (2026-10-16). This round adds:

| File | What |
|---|---|
| `00-existing.md` | The facts each package needs about llmpro and gitpro today |
| `DECISIONS.md` | The decisions this round rests on (I1 to I8; round 2: H1 to H4) |
| `02-rename.md` ... `08-bundle.md` | One work package each |
| `09-lessons.md` ... `12-harness-manifest.md` | Round 2 (2026-10-20): ideas from harness2 |
| `13-harness-place.md`, `14-harness-claude-plugin.md` | Round 3 (2026-10-20): setup places the harness |
| `15-sessions-list.md` ... `20-sessions-takeover.md` | Round 4 (2026-10-21): live sessions, version 1 (S1 to S11) |
| `21-releasepro.md` | Round 5 (2026-10-22): releases run by releasepro, the version in one file |
| `22-push-guard-budget.md` | Round 6 (2026-10-22): the leak cache survives a release; the push guard keeps to a time budget |
| `23-time-limits-and-caches.md` | Round 6 (2026-10-23): `check --budget` and a time limit for the MCP check; one budget for the agent hook; the sync's no-op cache survives a release and is private |
| `24-basic-auth-and-blocklist.md` | Round 7 (2026-10-23): HTTP Basic credentials are masked and found; a blocklist of fingerprints for secrets no rule finds |

Each package file is self-contained: an agent that has read this file, `00-existing.md`,
`DECISIONS.md` and its own package file has everything it needs. Where a package uses something an
earlier package made, its file repeats the signature it uses.

Status: draft, 2026-10-17. Proposed by Claude while the user was away; the user reviews it.

## 1. Waves

| Wave | Packages | Runs |
|---|---|---|
| 1 | 02 rename to llmpro, `--version --json`, XDG state | alone |
| 2 | 03 plugin contract, registry, git adapter, test fixtures | alone |
| 3 | 04 MCP, 05 setup, 06 push guard, 07 privacy by identity | in parallel |
| 4 | 08 skill, Claude Code plugin, README | alone |
| 5 | 09 lessons, 10 MCP gate, 12 harness manifest | in parallel |
| 6 | 11 harness commit per chat | alone |
| 7 | 13 setup places the harness | alone |
| 8 | 14 Claude Code hooks and guardrails through a store plugin | alone |
| 9 | 15 sessions on this machine (`llmpro resume` lists) | alone |
| 10 | 16 sessions on other machines, MCP `sessions` | alone |
| 11 | 17 continue a session | alone |
| 12 | 18 watch and notices | alone |
| 13 | 19 send a message to a session | alone |
| 14 | 20 take over an idle session (can be left out) | alone |

Round 4 runs one package per wave: each builds on the one before, and they share `sessions/__init__.py`
and `cli.py`. Before a wave, the integrator checks the facts DECISIONS S10 lists for it and writes them
into the package's "Facts" section.

After each wave the integrator (not a package) merges the branches, runs the whole suite and edits
`CHANGELOG.md`. No package edits `CHANGELOG.md`.

## 2. Rules for every package

1. **Tests first.** Write the package's test file, see it fail, then write the code.
2. **One test file per package**, named in the package file. A package may also fix imports or names in
   other test files only where its package file says so.
3. **"May edit" is exhaustive.** A package edits only the files its package file lists under "May edit",
   plus the new files it lists under "Creates". Within a wave no two packages edit the same file. If you
   find you need another file, stop and say so in your report instead of editing it.
4. **Offline, with a temporary HOME.** Tests use the fixtures in `tests/conftest.py` (`home`, `store`,
   `machine`, `onpath`, and from 03 on `fake_gitpro` and `tools_dir`). No test calls a real `gitpro`,
   `claude`, `codex` or the network, or reads the real home. From package 03 on, every outside program
   llmpro calls is found through `llmpro.tools.which(name)`, which the tests point at a temporary folder.
5. **No new runtime dependencies.** Standard library only, Python 3.11 and later. `pytest` for tests.
6. **Keep the hot path lean.** `llmpro hook ...` and `llmpro merge-driver ...` must not import argparse,
   the importers, the plugin registry or `importlib.metadata`.
7. **Preview, confirm, undo.** Anything that changes the user's machine goes through `setup`'s one
   preview and one question, and leaves an undo line. Nothing writes a file the user writes by hand, with
   one exception: the push guard's `chain_to` line in gitpro's config, added after a copy (DECISIONS I5).
8. **`--json` uses the envelope** `{"ok": bool, "command": "<name>", ...}` from 02 (`llmpro.jsonout`).
   Keys that a command printed before stay, with the same meaning.
9. **Exit codes**: 0 done, 1 a check or step failed or something is missing (`-n`), 2 usage or other
   error, 130 interrupted. One exception: `llmpro resume TARGET` runs an assistant in the terminal and exits
   with that assistant's own status, as `exec` would.
10. **No personal data in the repo**: no names, hosts, machine paths or client words in code, tests,
    templates or docs. Use `example.com`, `work`, `personal`, `~/code/...`.
11. **Plain English** in messages and docs: short sentences, no em dashes.
12. **Done** means: the package's tests pass, the whole suite passes with no new skips
    (`.venv/bin/pytest -q`), `python -m build` and `tests/smoke_wheel.py` pass, and the report lists every
    file changed.

## 3. How a package is built

Each package is built by one agent in its own git worktree on a branch `spec/NN-<name>`, from llmpro
`main` after the previous wave was merged. The agent commits on that branch (author: the repo default,
plus the `Co-Authored-By` line it is given) and never pushes. The integrator reviews the diff against
the package file, merges with `--no-ff`, and runs the full gates.
