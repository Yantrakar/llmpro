# Releasing

A release is one run of the Release workflow, started by hand on `main`. While the code is
private it makes a tag and a GitHub release in this repo, with the wheel, the sdist and
`SHA256SUMS`. Nothing goes to PyPI or the MCP Registry.

## Make a release

1. Set the version in `src/agentspro/__init__.py`.
2. In `CHANGELOG.md`, give that version a section headed `## X.Y.Z (YYYY-MM-DD)`, with
   the release date. A section marked `(unreleased)` stops the run.
3. Commit, and push to `main`.
4. Start the run: Actions > Release > Run workflow, on `main`. Or:

       gh workflow run release.yml --repo sandeepshelke/llmpro --ref main

5. The run checks the version, the changelog section and that tag `vX.Y.Z` does not exist
   yet. It runs CI on the same commit, builds and smoke-tests the wheel, then creates tag
   `vX.Y.Z` and the release, with the changelog section as its notes.

To install a release on a machine:

    pipx install --force git+ssh://git@github.com/sandeepshelke/llmpro.git@vX.Y.Z

## Going public

PyPI and the MCP Registry are written into `release.yml` and switched off. In order:

1. Rename the package to `llmpro` in `pyproject.toml`. The Release check fails while
   `PUBLISH_PYPI` is on and the name is anything else.
2. Make the code public: make this repo public, or move it to the Yantrakar org (needed for
   the MCP name, step 5).
3. On PyPI, in the `llmpro` project's settings, add a trusted publisher: this repo, workflow
   `release.yml`, environment `pypi`. Create the `pypi` environment in the repo's settings,
   with yourself as required reviewer.
4. Set the repository variable `PUBLISH_PYPI` to `true`.
5. For the MCP Registry: add `server.json` (name `io.github.Yantrakar/llmpro`, version and
   package version equal to the release) and the line
   `<!-- mcp-name: io.github.Yantrakar/llmpro -->` to `README.md`. The registry's GitHub
   sign-in only grants names under the repo's owner, so this needs the repo in the
   Yantrakar org. Create the `mcp-registry` environment, then set `PUBLISH_MCP` to `true`.

The VS Code extension `yantrakar.llmpro` is a placeholder from `names/vscode`; there is no
extension build yet.
