# 22 The push guard keeps to a time budget

Round 6 (2026-10-22). A fix to 06 (push guard) and to the leak cache in `check.py`.

## Facts

- On 2026-10-22, the first push after installing llmpro 0.1.1 ran the push guard (`llmpro hook push-guard`,
  through gitpro's `[guard] chain_to`) for about 16 minutes at full CPU, reading the raw logs under
  `~/.agents/sessions` (3.8 GB; one Copilot chat log of 87 MB). git had opened the SSH connection before
  the hook ran; GitHub closed it ("Connection to github.com closed by remote host") and the push failed.
  The same push again took 2.8 s, with the cache built.
- `check.CACHE_VERSION` was `f'{__version__}+{REDACT_VERSION}+{RULES_VERSION}'`. `load_cache` drops a
  cache of another version, and `blob_cache_key` carries the same string, so every release dropped the
  leak cache and the history blob cache, and the next check collected every raw source again.
- `check.py` imports nothing from `redact.py` (its docstring: "a gap in one is not a gap in both"), so
  `REDACT_VERSION` does not change what it collects. `RULES_VERSION` is raised by hand when the
  collection rules change.
- The guard calls `check.scan_texts(store, texts, jobs=1)`: no worker pool, and no time limit.
- gitpro's `pre-push` hook runs its own check under `timeout --foreground 30`, then runs the `chain_to`
  hook with no time limit.
- `importer/all.py` keys the sync's no-op cache on `f'{__version__}+{REDACT_VERSION}'`. It does not run
  in the push guard; 23 changes it.

## Changes

### 1. The leak cache survives a release (`check.py`)

`CACHE_VERSION = f'rules{RULES_VERSION}'`. The cache and the blob cache are dropped when the rules
change, not when the version does. The first check after upgrading to this release still collects once,
since the key changes form.

A test pins a sha256 of the code that decides what is collected and how it is hashed, next to
`RULES_VERSION`: the module text from `# ---- part 1 patterns` up to `THIS_MACHINE = MACHINE`, and the
source of `public_prefix`, `piece_ok` and `hashed_entry`. When that code changes the test fails, and the
person changing it either raises `RULES_VERSION` (the change alters what is collected) or only updates the
pin (it does not). The test's message says so.

### 2. A time budget for the guard (`check.py`, `pushguard.py`)

1. `pushguard.BUDGET = 20` seconds, counted from the start of the guard. It leaves room under gitpro's
   30 s for its own check. The exact idle time after which GitHub closes the connection is not known to
   us; 16 minutes was too long and 3 s was fine.
2. `scan_texts(store, texts, jobs=1, deadline=None, late=None)`. `deadline` is a `time.monotonic()` value;
   `late`, a list, receives the raw sources that were not read in full by then. Part 1 (formats) always
   runs in full.
3. `part2_cached(..., deadline=None, late=None)`, with a deadline:
   - The sources to read are taken in order of the bytes left to read, fewest first (a growing chat log
     reads only what it gained), so the most sources are brought up to date in the time.
   - No source is started after the deadline. One not started keeps its earlier cache entry, if any, as it
     is, and is read again next time.
   - A `.jsonl` source started before the deadline is read line by line and stops after the first complete
     line read past it. What was read is kept in the cache with the offset reached, so the next run goes on
     from there. Its stored signature carries the size read, not the file's size, so the next run does not
     take it as up to date.
   - Any other source (a database session, a folder, a `.json` file) is read whole once started.
   - Each source not read in full goes into `late`.
   - Matching uses every value the cache holds for each source, read now or earlier. The cache is saved as
     today, with what was read.
   - Showing a hit still reads its source again in full, as today; this can pass the budget, but the push
     is stopped then anyway.
   Without a deadline (`llmpro check`, the store's hooks), nothing changes.
4. When `late` is not empty after the scan, the guard prints one line to stderr, hit or not:
   `llmpro: the leak check had no time to read N raw logs in full; this push was checked against what the cache holds. Run llmpro check to bring the cache up to date.`
   The exit status is as before: 1 on a hit, else 0.

## Tests

`tests/test_push_guard.py`:

- `CACHE_VERSION` names `RULES_VERSION` and not `__version__`; a cache saved under another
  `CACHE_VERSION` is dropped by `load_cache`.
- The pin: the sha256 described in change 1 equals the one recorded for the current `RULES_VERSION`.
- With `BUDGET = 0` and no cache, a push that adds a value from the raw session file goes through (the
  file was not read in time), stderr has the "no time" line, and nothing is cached for that file. A run
  with the normal budget then stops the same push.
- With `BUDGET = 0` and no cache, a push that adds a known token format is still stopped.
- With a clock that passes the deadline part way through a long session file: the value on its last line is
  not found, the file is cached at an offset short of its end, and the next run without a deadline reads on
  and finds it.
- Under a deadline, a small source is read before a large one.
- With a warm cache, nothing is late and the guard prints nothing on a clean push (the existing tests).

## Release

0.1.2: a new first line in `src/llmpro/VERSION` and `## 0.1.2 (2026-10-23)` in `CHANGELOG.md`. The user
runs the release.
