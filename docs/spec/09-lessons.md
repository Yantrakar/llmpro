# 09 Lessons with evidence

Wave 5. Branch `spec/09-lessons`. Test file `tests/test_lessons.py`.

## Goal

A feedback or project memory can say what triggered it, which chats show it, and how sure it is
(DECISIONS H1). Assistants are told to write at most one lesson per session, or none, and to say
"confounded" rather than guess when two things changed at once. `llmpro check` catches a lesson that
cites a chat that is not there.

## May edit

`src/llmpro/templates/FORMAT.md`, `src/llmpro/templates/AGENTS.md`, `src/llmpro/health.py`,
`src/llmpro/importer/memory.py`, `skills/llmpro/SKILL.md`, `skills/llmpro/references/memory.md`.

## Creates

`tests/test_lessons.py`.

## Uses

`health.problems(store)` (one line per kind of problem; any line fails `check`), `read_frontmatter(path)`
(handles `[a, b]` lists, as `sources:` does), `importer/memory.py` `write_memory(store, fields, body)`
with its field `order`, and `run()`, which keeps fields edited in the store when the source memory is
unchanged (the loop near `for k in ('scope', 'status', 'superseded_by', 'created')`).

## Requirements

### The format (`templates/FORMAT.md`)

1. The memory file block gains three optional lines, after `sources:`:
   ```
   cue: one line: the situation in which this applies
   evidence: [history/<machine>/<tool>/<YYYY>/<file>.md, <tool>:<original id>, ...]
   confidence: high | medium | low
   ```
   and a short paragraph under it, "Lessons":
   - A lesson is a feedback or project memory that carries `cue` and `evidence`.
   - Evidence names the chats that show it: a path under `history/`, or a source id `<tool>:<id>`.
     Never a path under `local/` or `sessions/` (those names would leave the machine); use the source
     id instead.
   - `confidence`: high when the user said it or it held in two or more chats; medium for one clear
     chat; low otherwise.
   - At most one new lesson per session, often none. If two things changed at once and you cannot tell
     which one helped, write that it is confounded, or write nothing; do not guess.
2. Credit in one line at the end of that paragraph: "The lesson fields follow the experience notes of
   Google Research's harness2 (Apache 2.0); no code was copied."

### The rules for assistants

3. `templates/AGENTS.md`, section "Adding or changing a memory": one new bullet, two sentences at most:
   at most one lesson per session or none; give `cue`, `evidence` and `confidence` as FORMAT.md says,
   and say "confounded" rather than guess. Keep the file's tone and length.
4. `skills/llmpro/references/memory.md`: a section "Lessons" with the same rules as item 1 and one short
   example memory (made-up content, no personal data). `skills/llmpro/SKILL.md`: one line pointing at
   it. The body stays under 150 lines.

### The check (`health.py`)

5. For each memory with `evidence`:
   - `evidence` must be a list (a single string is also accepted as a list of one).
   - An entry that starts with `history/` must name an existing file in the store.
   - An entry that starts with `local/` or `sessions/`, or is an absolute path or contains `..`, is a
     problem whatever exists.
   - Any other entry must look like a source id: `^[a-z][a-z0-9-]*:\S+$`.
6. `confidence`, when present, must be `high`, `medium` or `low`.
7. One problem line for all of these: `N memories cite evidence that is wrong (<what is wrong in each>):
   <named list>`, using `named()` like the other lines. A memory without `cue`, `evidence` or
   `confidence` is not a problem: imported tool memories have none.

### The importer (`importer/memory.py`)

8. `write_memory` writes `cue`, `evidence`, `confidence` after `sources` when present.
9. A Claude source memory whose frontmatter has any of the three keeps them (redacted like
   `description`; `evidence` entries are not redacted but a `local/` or `sessions/` entry is dropped).
10. A store memory whose source is unchanged keeps the three as edited in the store, like `scope`.
    Update the module docstring line that lists the kept fields.

## Tests (`tests/test_lessons.py`)

- A memory with `cue`, valid `evidence` (an existing history file and a source id) and `confidence:
  medium`: `llmpro check` passes (no new problem).
- Each of: a missing history path, `local/history/...`, `sessions/x`, `../x`, `/abs`, `confidence:
  sure`: exactly one problem line, naming the memory; `check` exits 1.
- A memory without the three fields: no problem.
- Importer: a Claude memory with the three fields in its source comes out with them in that order after
  `sources`; a `local/...` entry is dropped; re-running with the source unchanged keeps a store edit to
  `cue`.
- `FORMAT.md` template contains `cue:`, `evidence:`, `confidence:` and the harness2 credit line.
