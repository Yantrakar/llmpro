# 21 Releases run by releasepro

Round 5 (2026-10-22). llmpro 0.1.0 was released by its own Release workflow: started by hand, with no
gate before the GitHub release, and the version written in two places (`src/llmpro/__init__.py` and
`.claude-plugin/plugin.json`). From 0.1.1 on, releasepro (https://github.com/Yantrakar/releasepro) runs
the release, as it does for gitpro and for itself: a person says yes at each channel's gate, no token is
stored, and every channel is checked until it lists the version.

## Facts

- releasepro 0.2.2 reads `releasepro.toml` at the repo root. The version is the first word of line 1 of
  `[release] version_file`. `releasepro notes X.Y.Z --check` exits 0 when the changelog has a section
  `## X.Y.Z`; `--write DIR` writes `RELEASE_NOTES.md` and `ANNOUNCEMENT.md`.
- `releasepro run --as LOGIN` starts `release.yml` on the branch and approves a pending gate only after
  the person's yes, and only for a gate it knows (`release`, `pypi`, `mcp-registry` among them).
- A job that names an environment that does not exist creates it with no reviewer. So the `release`
  environment must exist, with a required reviewer, before the first run. Today the repo has only
  `pypi` (reviewer, branch policy `main`).
- Claude Code's `plugin.json` `version` is optional. Without it, Claude Code uses the commit of the
  plugin's folder to see an update; with it, a version left unchanged keeps users on the cached copy.
  gitpro's `plugin.json` has no version.
- The repository variable `PUBLISH_PYPI` is `true`; `PUBLISH_MCP` is not set and there is no
  `server.json`.

## Changes

1. **One version file.** `src/llmpro/VERSION`; line 1 is `X.Y.Z  YYYY-MM-DD  <one line>`, newest first.
   `pyproject.toml` reads it (`[tool.hatch.version] path` and `pattern = '\A(?P<version>\d+\.\d+\.\d+)'`).
   `__init__.py` sets `__version__` to the first word of its first line, so `llmpro --version`, the MCP
   `serverInfo` and the cache keys built on `__version__` are unchanged in form. The version is written
   nowhere else.
2. **No version in `plugin.json`.** `.claude-plugin/plugin.json` loses `version`; the test checks it is
   absent from `plugin.json` and `marketplace.json`. This replaces 08, items 7 and 10.
3. **`tests/smoke_wheel.py`** reads the version from `llmpro/VERSION` in the wheel, and checks the file
   is there.
4. **`releasepro.toml`**: `[release]` repo `Yantrakar/llmpro`, the version file, branch `main`, workflow
   `release.yml`, preflight `gitpro why`; `[channel.github]` assets `llmpro-{v}-py3-none-any.whl`,
   `llmpro-{v}.tar.gz`, `SHA256SUMS`, `ANNOUNCEMENT.md`; `[channel.pypi]` project `llmpro`; `[notes]`
   changelog and a one-line summary. No MCP Registry channel while `PUBLISH_MCP` is off.
5. **`release.yml`**, on releasepro's own as the model:
   - The releasepro pin is written once, as the workflow variable `RELEASEPRO`; check and build install
     it.
   - check: runs on `main`; `releasepro version` gives X.Y.Z, `releasepro notes --check` checks the
     changelog section, tag `vX.Y.Z` must not exist. The MCP Registry readiness step stays, under
     `PUBLISH_MCP`.
   - ci: `ci.yml`, as before. build: as before (wheel, sdist, `smoke_wheel.py`, `SHA256SUMS`), then
     `releasepro notes X.Y.Z --write notes`, uploaded as the `notes` artifact.
   - github: environment `release`. The tag is made through the API and is kept on a re-run if it
     already points at this commit; the release is created, or on a re-run its files and notes are
     replaced. Its body is `RELEASE_NOTES.md`; `ANNOUNCEMENT.md` is an asset.
   - pypi: environment `pypi`, trusted publishing, `skip-existing: true` so a re-run passes. It no
     longer depends on `PUBLISH_PYPI`: `releasepro.toml` names the channel, and a variable that could
     skip it would make `releasepro verify` fail on a release that ran as configured.
   - mcp-registry: unchanged, still under `PUBLISH_MCP`.
6. **`docs/releasing.md`** describes the releasepro steps and the one-time setup; the text about a
   private repo and a git install goes.
7. **`CHANGELOG.md`**: `## 0.1.1 (2026-10-22)`. Its header points at `docs/releasing.md` as before.

## Tests

`tests/test_release.py`:
- `llmpro.__version__` equals the first word of `src/llmpro/VERSION`, which is X.Y.Z.
- No other file in `src/`, `.claude-plugin/` or `pyproject.toml` writes that version.
- `releasepro.toml` names the version file, the four assets, the PyPI project and the changelog.
- `release.yml` sets `RELEASEPRO: releasepro==X.Y.Z` once, installs only `"$RELEASEPRO"`, gives the
  github job the environment `release`, the pypi job the environment `pypi` and `skip-existing: true`,
  and does not mention `PUBLISH_PYPI`.
- `CHANGELOG.md` has a section for the version (`releasepro notes --check` in CI checks the same).

## Left to the person

Creating the `release` environment with a required reviewer and the branch policy `main`; starting the
run and answering each gate; deleting the `PUBLISH_PYPI` variable, which nothing reads any more.
