# 15 Sessions on this machine

Wave 9. Branch `spec/15-sessions-list`. Test file `tests/test_sessions_list.py`.

## Goal

`llmpro resume` with no target lists the open Claude Code, Copilot CLI and opencode sessions on this
machine in the shared states (DECISIONS S1 to S4). `--json` and `-n` give the same list as data. Other
machines come in 16, continuing a session in 17.

## May edit

`src/llmpro/cli.py`.

## Creates

`src/llmpro/sessions/__init__.py`, `src/llmpro/sessions/claude.py`, `src/llmpro/sessions/copilot.py`,
`src/llmpro/sessions/opencode.py`, `tests/test_sessions_list.py`, `tests/fixtures/sessions/` (records
made from real ones with every name, path and id replaced: `~/code/demo`, `demo`, made-up uuids).

## Uses

`store.MACHINE`, `store.configure(store, required=False)`, `store.is_local_project(folder)`,
`redact.redact(text)`, `jsonout.emit(command, obj, ok=True)`, `tools.which(name)`. Fixtures `home`,
`store`, `tools_dir`.

## Requirements

### Rows

1. `sessions.Row`, a dataclass: `tool` (`claude-code`, `copilot-cli`, `opencode`), `machine`, `id`, `name`
   (`''` when the tool has none), `folder`, `state` (S3), `since` (epoch seconds the state began, or
   `None`), `line` (S4, `''` when none), `pid` (or `None`), `where` (where it is open: `vscode`, `ssh`,
   `terminal` or `''`), `version` (the tool's, or `''`), `local_only` (bool), `error` (`''`). `Row.as_dict()`
   gives the JSON form; `Row.paths` (the record or lock file and the event log or transcript, as a
   tuple of paths, `None` when unknown) is left out of it. `Row.from_dict(d)` reads the JSON form back and refuses (ValueError) a dict with a missing
   key or a wrong type.
2. `sessions.STATES` is the tuple of S3 states in sort order. `sessions.sort(rows)` sorts by it, then by
   `since`, oldest first, with `None` last. `sessions.FORMAT = "llmpro-sessions.v1"`.
3. `sessions.clean(text, width=None)` removes control characters (C0, C1, and whole ESC
   sequences), joins lines with a space and, when `width` is given, cuts to it. Every string printed from
   a row goes through it (S4).
4. `sessions.ID = re.compile(r'[A-Za-z0-9-]+')`. A record whose id does not fully match is
   `not-readable` (S11).

### Adapters

5. Each adapter module has `CHECKED` (the tool versions it was checked against, as strings) and
   `rows(machine, line=True)` -> list of `Row`. An adapter never raises: an unreadable record is one row
   with state `not-readable` and `error` saying what was wrong, in one plain sentence. With `line=False`
   no transcript or event log is read beyond what the state needs.
6. `sessions._proc(pid)` -> `(uid, program, start_time)` from `/proc/<pid>`, or `None`; the one place that
   reads `/proc/<pid>` for the adapters, so tests can replace it. `program` is the base name of the first
   `cmdline` word, or `comm`. It never reads `environ`.
7. **Claude Code** (`claude.py`). Every `~/.claude/sessions/*.json` (never `*.key`). A record is used only
   when `pid` names a live process owned by this user whose program is `claude`, or `node` with a start
   time equal to `procStart` (the unit from "Facts"). A record that fails this is skipped, not shown.
   `status` maps by S3; another value is `unknown`. `since` is `statusUpdatedAt`. `line` for
   `needs-reply` is the start of the last assistant text in the transcript, found as
   `~/.claude/projects/*/<sessionId>.jsonl` and read backward from the end (at most the last 256 KB, whole
   lines only); for `needs-approval` it is `permission prompt`. `messagingSocketPath` is never used.
8. **Copilot CLI** (`copilot.py`). A session is open when `~/.copilot/session-state/<id>/inuse.<pid>.lock`
   names a live process of this user whose program is the one under "Facts", or when such a process has
   `--resume` followed by `<id>` or an id prefix in its command line. Command lines are matched, never
   printed. State: read `events.jsonl` backward (at most the last 256 KB, whole lines only), skip types in
   `BOOKKEEPING` (`session.usage_checkpoint`, any `model.*`), and map the first other type by S3;
   `permission.requested` gives the line from its `intention` field. The version is `copilotVersion` from
   `session.start` (read forward, first 64 KB). Do not import `llmpro.sources.copilot_cli`.
9. **opencode** (`opencode.py`). When `tools.which('opencode')` is None, no rows. Otherwise it runs the
   command recorded under "Facts" with a 3 second timeout and maps its JSON. A failure is one
   `not-readable` row naming the command and its exit status. It never reads opencode's database.
10. `where`: from the process's parents: `vscode` if a parent's program is `code` or `code-insiders`, else
    `ssh` if one is `sshd`, else `terminal`.
11. `line` is redacted, cleaned and cut at 120 characters. `is_local_project` is asked once per folder per
    listing. A local-only row has `line = ''`, `local_only = True`, and its transcript is not opened.
12. `sessions.local_rows(line=True)` -> the three adapters' rows together, sorted.

### The command

13. `llmpro resume [TARGET] [-n] [-y] [--json] [--local] [--machine NAME] [--watch] [--no-line]` is added
    to `cli.py`, and `resume` and `resume.json` to `CAPABILITIES`. This package handles the forms without
    TARGET and without `--watch`; the others print `llmpro resume: <form> comes in a later version` and
    exit 2. `--local` means this machine only; until 16 that is every form. `--no-line` leaves every
    `line` empty and reads no transcript (S4). `sessions.supported()` is true when `/proc/self` exists and
    `os.pidfd_open` is there; when it is false, `resume` prints `live sessions need Linux` and exits 2.
14. Human output, one row per line: a number, state, tool, `name or id prefix@machine`, folder with `~`,
    time in state (`4m`, `2h`), and the line. A header line names the columns. No color when piped or
    when `NO_COLOR` is set. No rows: `no open sessions` on stderr, exit 0.
15. `--json`: the envelope with `command: "resume"`, `format`, `machine`, `rows: [...]`, `errors: []` (16
    fills it). Exit 0 even when a row is `not-readable`.
16. On a terminal without `-n` and without `--json`, after the list llmpro asks `Continue which? (number
    or name, Enter to quit)`. Until 17 an answer prints `continuing a session comes in a later version`
    and exits 2. Enter alone exits 0.
17. Startup stays fast: listing reads no history, imports no importer and no plugin, and finishes in under
    a second with 20 records.

## Tests

- Records for each adapter from `tests/fixtures/sessions/`, with `sessions._proc` replaced: a live pid, a
  dead pid, a reused pid (start time differs), a `node` process, a record with a missing key, an id with a
  slash.
- Every S3 mapping for Claude and Copilot, a `model.*` event after `assistant.turn_end`, an unknown event
  type, a half-written last line.
- A Copilot session open with no lock (only the `--resume` command line).
- opencode: absent, a fake `opencode` in `tools_dir` that prints the recorded JSON, one that exits 1.
- Redaction and ESC sequences in `line`, in `name` and in `folder`; a local-only folder gives no line and
  the transcript is not opened (patch `open` to fail on it); `is_local_project` called once per folder;
  `--no-line`.
- `Row.from_dict` round trip and refusals. Sort order; human output; `--json` shape; `NO_COLOR`; the
  prompt with Enter alone.

## Facts (recorded by the integrator before this wave, S10.1, S10.2, S10.7)

To be filled in.
