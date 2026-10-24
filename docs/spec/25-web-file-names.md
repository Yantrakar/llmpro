# 25 Web file names are no secrets

Round 8 (2026-10-24). A fix to part 2 of the check, the collection from raw sources (`check.py`).

## Facts

- On 2026-10-23 the store's pre-commit check flagged a value C (`ffc09d6bfac1fac6`, as `check.fp`) in the
  person's store. C is 13 characters: 8 lowercase letters, a dot and `html`, the link of a page on a web site.
  The raw Copilot logs hold it under the JSON keys `staticTokens` and `possiblePaths`. The person allowlisted
  it the same day.
- `secret_field(key, v)` takes a random-looking value under a key that contains a secret name, and
  `staticTokens` contains `token`.
- `plausible(v)` turns down a file name only by `FILE_LIKE`, whose list of extensions has no web files
  (`html`, `css`, `js`). Its rule for dotted names then judges the 12 letters of C as one run, and
  `looks_random` finds them random, though the 8 letters before the dot read as a word (`wordlike`).
- `plausible` also serves `nearmiss.py`.

## Change

`FILE_LIKE` takes the extensions `html`, `htm`, `css`, `js`, `mjs`, `jsx`, `ts`, `tsx` and `svg` too. A value
that is a name of such a file is not collected, under any key, and is no near miss.

What is collected changes, so `RULES_VERSION` goes to 5, and the pin in `tests/test_push_guard.py` follows.
The next check collects every raw source again (spec 22); a push guard that runs first may say it did not
read every log in its budget.

Measured on the person's raw logs (736 files, Codex left out), old rules against new on every line that names
such a file: the new rules drop one value, C, and add none.

## Tests

`tests/test_redaction.py`, `test_a_web_file_name_is_no_secret`:

- `products.html`, `webinars.html` (both taken by the old rules under `staticTokens`), `index.htm`,
  `app.min.js`, `style.css`, `Page.tsx` and `logo.svg` are not plausible, and no secret under `staticTokens`.
- A random value with a dot is still a secret under `staticTokens`, and one with `.js` inside it is still
  plausible.

## Release

The next release. Once it is installed, C's allowlist entry is no longer needed; removing it is the person's
choice.
