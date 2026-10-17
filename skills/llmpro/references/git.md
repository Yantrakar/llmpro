# Git identities (needs gitpro)

llmpro does not do git identities itself. The separate tool gitpro does. llmpro finds the `gitpro` program ($LLMPRO_GITPRO, else PATH), reads `gitpro --version --json`, and uses it. If gitpro is missing or older than 0.4, the git features are hidden and nothing else breaks. To add it: `pipx install gitpro`, or `pipx install "llmpro[git]"`.

## What you get when gitpro is installed

- MCP tools with a `git_` prefix, from gitpro's current tools. For example `git_why` says which name, email and key a repo should use and what it uses now. Tools that write are not offered.
- `llmpro git ARGS...` runs `gitpro ARGS...` with the terminal passed through. For example `llmpro git why .`.
- `llmpro setup -n` shows what `gitpro setup` would do. After the user's one yes, llmpro runs `gitpro setup -y --agent none`. It uses `--agent none` because the llmpro plugin registers the MCP server and the agent hook already.
- In Claude Code, the llmpro plugin's hook runs `gitpro agent-check` before Bash calls. If gitpro's own plugin is also enabled, the hook runs twice. Keep one.

## Before you commit, push or open a pull request

Ask `git_why` (or `llmpro git why .`) first. If it reports a problem, tell the user before you commit. Do not fix it by editing git config yourself.

## gitpro's rules, in short

- Edit only the TOML file (`~/.config/gitpro.toml`). It is the one source of truth for identities.
- Run `gitpro setup -n` before `gitpro setup`. It shows what would change.
- Never read or print private keys.
- Suggest, and let the user pick. Do not guess an identity.

## local_identities

In the store's `config.toml`, `[privacy] local_identities = ["work"]` keeps the chats of projects that gitpro places under those identities in `local/`. They are never committed or sent to the hub. When llmpro cannot place a folder, its chats stay local. If gitpro is missing or too old while this is set, `llmpro sync` stops and says why.

## More

gitpro's own guide: https://github.com/Yantrakar/gitpro/blob/main/docs/guide.md
