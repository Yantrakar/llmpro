# 08 Skill, Claude Code plugin, agent hook, README

Wave 4 (after 04 to 07 are merged). Branch `spec/08-bundle`. Test file `tests/test_bundle.py`.

## Goal

One thing for each assistant to load: a skill that explains llmpro and its git part, a Claude Code plugin
that brings the skill, the MCP server and the agent hook (DECISIONS I8), and a README that a new user can
follow. Assistants other than Claude Code get the skill through the store's `skills/` folder, which they
already read.

## May edit

`README.md`, `src/llmpro/templates/AGENTS.md`, `src/llmpro/hot.py`, `src/llmpro/plugins/memory.py`,
`pyproject.toml` (package data only), `tests/smoke_wheel.py`.

## Creates

`skills/llmpro/SKILL.md`, `skills/llmpro/references/memory.md`, `skills/llmpro/references/secrets.md`,
`skills/llmpro/references/git.md`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
`.mcp.json`, `hooks/hooks.json`, `src/llmpro/agentcheck.py`, `tests/test_bundle.py`.

## Uses

From 03 to 07: `gitpro.get()`, `tools.which`, `plugins.base.SetupItem`, `setup.Item`, `setup.Undo`,
`setup.state_dir()`, `register.items` (05, in `plugins/memory.py`), the push guard (06) and
`local_identities` (07), which the docs describe.

## Requirements

### The skill (`skills/llmpro/`)

1. `SKILL.md` follows the Agent Skills format: front matter with `name: llmpro`, a `description` of at
   most 1024 characters that says when to use it (the user asks about past chats or memories, wants
   something remembered, before committing or pushing, git identity questions, secrets in a repo), and
   `license: MIT`. The body stays under 150 lines: what the store is, the rules (never push the store,
   never store secrets, run `llmpro check` before a commit in the store, `-n` before any `setup`, never
   run `setup -y` or `uninstall -y` without the user's yes in this conversation), the commands, and
   pointers to the three references.
2. `references/memory.md`: finding past chats (`llmpro find`, the MCP `find` tool, grep over `history/`),
   adding a memory (format from `FORMAT.md`), the index.
3. `references/secrets.md`: `llmpro check` and what its two parts find, the allowlist by fingerprint, the
   push guard and `LLMPRO_SKIP`, never printing a value.
4. `references/git.md`: what the `git_*` MCP tools and `llmpro git ...` give when gitpro is installed,
   gitpro's own rules in short (edit only the TOML, `gitpro setup -n` first, never read private keys),
   how `local_identities` uses it, and that gitpro's guide is at
   `https://github.com/Yantrakar/gitpro/blob/main/docs/guide.md`. Do not copy gitpro's skill.
5. The skill folder ships in the wheel as package data (`llmpro/skills/llmpro/...`; keep the repo copy
   at the top as the source, and have the build include it, for example with hatch `force-include`).

### The skill in the store (`plugins/memory.py`)

6. A setup item `skill: llmpro` (after 05's items): wanted is `STORE/skills/llmpro/` equal to the copy in
   the installed package, with a first line in `SKILL.md`'s body
   `<!-- installed by llmpro setup; llmpro setup updates it, edits here are overwritten -->`.
   - Missing or older (differs and still has that line): missing; apply writes it (undo restores the
     earlier files or removes the folder).
   - Differs and the line was taken out by the user: present; detail `edited by hand; llmpro setup leaves
     it`.
   - The store's sync commits it like any other skill.

### The Claude Code plugin

7. `.claude-plugin/plugin.json`: `name` `llmpro`, `version` the package version, a one-line
   `description`, `author` `{"name": "Yantrakar"}`, `repository` `https://github.com/Yantrakar/llmpro`,
   `license` `MIT`. `.claude-plugin/marketplace.json`: one marketplace named `llmpro` listing this plugin
   with `source` `./`. `skills/` is picked up from the plugin root.
8. `.mcp.json`: `{"mcpServers": {"llmpro": {"type": "stdio", "command": "llmpro", "args": ["mcp"]}}}`.
9. `hooks/hooks.json`: one `PreToolUse` hook, matcher `Bash`, timeout 10:
   `sh -c 'command -v llmpro >/dev/null 2>&1 || exit 0; llmpro hook agent-check; exit 0'`.
10. A test checks that `plugin.json`'s `version` equals `llmpro.__version__`, so a release cannot forget it.
    (`docs/releasing.md` is not in "May edit"; say in the report that it should mention the bump.)

### The agent hook (`hot.py`, `agentcheck.py`)

11. `llmpro hook agent-check` reads the hook event from stdin (at most 1 MB) and, when gitpro is there
    and lists the capability `agent-check`, runs `gitpro agent-check --agent claude` with that same
    input, passing its stdout and exit code through. Otherwise it prints nothing and exits 0.
12. It runs on every Bash call of the assistant, so it is lean: no argparse, no registry, no
    `importlib.metadata`. It does not run `gitpro --version --json` each time: it keeps
    `state_dir()/gitpro-version.json` with gitpro's path, size, mtime and capabilities, and asks again
    only when one of those changed. It finds gitpro as 03's adapter does (`$LLMPRO_GITPRO`, else
    `tools.which`); import only those two small modules.
13. Any failure (gitpro crashes, times out after 8 s, the cache is broken): print nothing, exit 0. The
    hook never blocks the assistant because of llmpro.
14. Setup (05) keeps passing `--agent none` to gitpro; this hook replaces gitpro's own. If gitpro's own
    plugin is enabled as well, the hook runs twice; the README says to keep one.

### Docs

15. `README.md`, rewritten for a new user, in plain English and under 200 lines: what llmpro is (one
    store for every assistant's chats, memories, instructions and skills, kept free of secrets), the
    install (`pipx install llmpro`, and `pipx install "llmpro[git]"` or `pipx install gitpro` for the git
    part), `llmpro setup -n` then `llmpro setup`, the commands with one line each, the MCP server and the
    Claude Code plugin (`/plugin marketplace add Yantrakar/llmpro`, then `/plugin install
    llmpro@llmpro`), the push guard with the `chain_to` line, `local_identities`, how gitpro fits (it
    stays a separate tool; llmpro finds it), and uninstall. No machine names, no personal paths.
16. `templates/AGENTS.md`: `agentspro` already reads `llmpro` (02); add two short points: the `git_*`
    tools and `llmpro git ...` when gitpro is installed, and that chats of the identities in
    `[privacy] local_identities` stay in `local/`. Keep its tone and length.

## Tests (`tests/test_bundle.py`)

- `SKILL.md` front matter parses (a small parser in the test, no PyYAML needed), `name` is `llmpro`, the
  description is at most 1024 characters, the body under 150 lines; each reference the body links exists.
- `plugin.json`, `marketplace.json`, `.mcp.json`, `hooks.json` parse; versions match; the hook command
  is exactly the one above.
- `llmpro hook agent-check` with no gitpro: no output, exit 0, and `argparse`, `importlib.metadata` and
  the built-in plugin modules (`llmpro.plugins.memory`, `.secrets`, `.git`) are not in `sys.modules`
  (`llmpro.gitpro` imports `llmpro.plugins.base`, which is fine) (run it in a child Python and print `sys.modules` keys at exit, or check with `-X
  importtime`).
- With the fake gitpro listing `agent-check` and answering `['agent-check', '--agent', 'claude']` with a
  JSON reply and exit 0: the reply is passed through, and the fake saw the same stdin. A second run does
  not call `--version --json` again (check `fg.calls()`); touching the fake makes it ask again.
- With the fake not listing `agent-check`: no output, exit 0, and `agent-check` was never run.
- A fake that sleeps 20 s: exit 0 within 10 s.
- The skill item: missing in a fresh store; `setup -y` writes it; the undo removes it; an edited copy
  without the marker line is left alone.
- The wheel (`smoke_wheel.py`) holds `llmpro/skills/llmpro/SKILL.md`.

## Done when

README section 2 item 12 holds, and a manual check is listed in the report for the integrator: `claude
--plugin-dir .` loads the plugin (the integrator runs it; agents do not run `claude`).
