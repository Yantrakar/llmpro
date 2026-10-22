# Releasing

A release is one run of the Release workflow on `main`, started and followed by
[releasepro](https://github.com/Yantrakar/releasepro). It reads `releasepro.toml`. The run checks the
version, runs CI, builds once, then publishes to each channel after a person's yes at that channel's
gate: the GitHub release (environment `release`), then PyPI (environment `pypi`). No token is stored:
PyPI trusts this repo's `release.yml` and the `pypi` environment. Spec: `docs/spec/21-releasepro.md`.

## Make a release

1. Write the new version as the first line of `src/llmpro/VERSION`:
   `X.Y.Z  YYYY-MM-DD  <one line on what changed>`, above the older lines. The version is written
   nowhere else: the package, `llmpro --version` and the MCP server read this file, and the Claude Code
   plugin has no version (Claude Code sees an update from the commit).
2. In `CHANGELOG.md`, give that version a section `## X.Y.Z (YYYY-MM-DD)` saying what changed for
   users. The release notes and the announcement draft are made from it. Check it with
   `releasepro notes X.Y.Z --check`.
3. Commit, and push to `main`. The local `main` must equal `origin/main`.
4. Start the run and answer each gate:

       releasepro run --as LOGIN

   If it stops (a failed job, a gate answered no, Ctrl-C), run it again: it picks the run up.
5. Check every channel lists the version:

       releasepro verify X.Y.Z

6. The announcement drafts: `releasepro notes X.Y.Z --announcement`, or `ANNOUNCEMENT.md` on the
   GitHub release.

To install a release on a machine: `pipx install llmpro`, or `pipx upgrade llmpro`.

## Set up once

- The `release` and `pypi` environments, each with a required reviewer and the branch policy `main`. A
  job that names an environment that does not exist creates it with no reviewer, so create both before
  the first run.
- On PyPI, the `llmpro` project's trusted publisher: this repo, workflow `release.yml`, environment
  `pypi`.

## The MCP Registry

The `mcp-registry` job runs when the repository variable `PUBLISH_MCP` is `true`. Before turning it on:

1. Add `server.json` (name `io.github.Yantrakar/llmpro`) and the line
   `<!-- mcp-name: io.github.Yantrakar/llmpro -->` to `README.md`. The registry's GitHub sign-in grants
   names under the repo's owner, here `io.github.Yantrakar/*`. Today the check and `mcp-registry.yml`
   want the version written in `server.json`, a second place; change them to stamp it at build time,
   as gitpro does, so the version stays in `src/llmpro/VERSION` only.
2. Create the `mcp-registry` environment with a required reviewer.
3. Add `[channel.mcp-registry]` with that name to `releasepro.toml`, so `releasepro verify` checks it.
4. Set `PUBLISH_MCP` to `true`.

The VS Code extension `yantrakar.llmpro` is a placeholder from `names/vscode`; there is no
extension build yet.
