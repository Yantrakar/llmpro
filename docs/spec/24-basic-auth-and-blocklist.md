# 24 Two secrets the redactor missed: HTTP Basic credentials, and a list of fingerprints

Round 7 (2026-10-23). A fix to the redactor (`redact.py`) and to part 1 of the check (`check.py`).

## Facts

- On 2026-10-23 a sync on mukti (0.1.2) was stopped by the store's pre-commit check. The new history file of a
  Copilot CLI chat held a line from a phone app's logs: a JSON array of strings, with two of the person's
  secrets in it. Nothing was committed. The values are known here only by fingerprint (sha256, first 16 hex
  digits, as `check.fp`).
- B (`74dd9722a1fa8446`): the element `"Basic <B>"`, where B is 40 characters of base64 that decode to 29
  printable bytes with one colon, an HTTP Basic `user:password`.
  - `redact.py` masks Basic only after an `authorization` key (`_AUTH_HEADER`); `_BEARER` takes Bearer only.
  - Part 1 of the check has no Basic rule.
  - Part 2 collects it from the raw log (`COLLECT`, `auth-header`), so the leak check flags it, but only
    where that raw log is.
- A (`13727f3959eca93f`): 16 characters, letters and digits with two `%` that are not percent-encoding.
  Nothing around it says it is a secret, and no shape tells it from the random ids, hashes and nonces in the
  logs. The person says it is a secret.
- A also sits in a tool output in another Claude Code chat on mukti, which the next sync imports.
- The store has an allowlist, `.agentspro/allowlist.json`: `{fingerprint: reason}`, edited by the person.
- `check.scan_patterns` serves part 1 and the collection from raw sources too (`check.py`, in
  `collect_text`), so a change there changes what is collected and needs `RULES_VERSION` raised: a full
  re-read of every raw log (spec 22).

## Changes

### 1. HTTP Basic credentials

The shape: `basic` (any case), spaces or tabs, then a run of 12 or more base64 characters with up to two `=`,
not followed by another base64 character, that decodes to UTF-8 text with no control characters and a colon
with something on each side.

- `redact()`: the run becomes `[REDACTED:auth-header]`. The run is learned (sure), and so is what follows the
  first colon (the password), under `learn()`'s own rules.
- `llmpro check` part 1 and the push guard: a hit of kind `auth-header`.

### 2. A list of fingerprints: `.agentspro/blocklist.json`

`{fingerprint: reason}`, like the allowlist, edited by the person, for a value that is a secret but has no
shape a rule can find. It is committed and reaches every machine through the hub. A fingerprint of a short
or common password can be found again by trying candidates, so list long random values only. The
skill's note on secrets (`skills/llmpro/references/secrets.md`) says so, and `FORMAT.md` names the file.

- Candidates in a text: each run of characters other than whitespace and `` "'`<>()[]{},; `` (as
  `check.RUN`); that run without `.`, `:` and `\` at its end; and its parts between `=`, `:`, `&`, `?`, `/`,
  `@` and `\`. Those of 6 to 256 characters are hashed.
- `redact()`: a candidate whose fingerprint is listed becomes `[REDACTED:listed]` and is learned (sure), so
  the sweep also removes a copy that sits inside a longer run. `apply_config()` loads the list from the
  configured store. With no list, `redact()` does no extra work.
- `llmpro check` part 1 and the push guard: a hit of kind `listed`, unless the fingerprint is also
  allowlisted.
- The sync's no-op cache key (spec 23) gains a hash of the listed fingerprints, so a file skipped before is
  redacted again when the list changes.

### Where the check code goes

Both rules are in a new `check.scan_more(text)`, in the "the two checks" section, which part 1 and
`scan_texts` call after `scan_patterns`. The collection does not call it. So `RULES_VERSION` and its pin
stay as they are. The leak cache is unchanged.

The history blob cache (`--history`, `.git/agentspro/blobs.json`) keeps blobs that passed part 1, and its
key carries `CACHE_VERSION`, which these rules do not change. So `blob_cache_key` gains `more`:
`check.MORE_VERSION` (1, raised by hand when `scan_more` finds something it did not) and a hash of the listed
fingerprints. A history blob passed before is checked again when either changes.

## Tests

`tests/test_redact_listed.py`, and the push guard in `tests/test_push_guard.py`, with made-up values of the
same shapes, built at run time:

- The JSON array line: B is masked by `redact()`, and found by `scan_more` as `auth-header`. Without a list,
  A is left as it is.
- With A's fingerprint listed, `redact()` masks A in the line, in `"x=A&y=1"`, in `` `A` `` and as a JSON-escaped
  `\"A\"`. The sweep then removes a copy glued inside a longer run. `scan_more` reports it as `listed`.
- Not masked: `Basic` before a word, base64 that decodes to bytes that are not text, base64 text with no
  colon, or with nothing before or after it. A value whose fingerprint is not listed is left alone.
- The sync's no-op key changes when the list changes, and does not change without one.
- `llmpro check` fails on a store file holding A when A is listed, and on one holding `Basic B`; it passes when
  A is allowlisted too. The output names the kind, not the value.
- A list that is not `{"<16 hex digits>": reason}` stops `redact` and the check with an error.
- The history blob cache key changes when the list changes.
- The push guard lets a push that adds A through while A is not listed, and stops one once it is; it stops a
  push that adds `Basic B`.

## Release

0.1.3. The person lists A in `.agentspro/blocklist.json` of the store, upgrades llmpro and runs the sync
again. The changed redaction code drops the no-op cache, so every store file is redacted again once.
