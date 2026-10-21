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
   `terminal`, `container` or `''`), `version` (the tool's, or `''`), `local_only` (bool), `error` (`''`). `Row.as_dict()`
   gives the JSON form; `Row.paths` (the record or lock file and the event log or transcript, as a
   tuple of paths, `None` when unknown) is left out of it. `Row.from_dict(d)` reads the JSON form back and refuses (ValueError) a dict with a missing
   key or a wrong type.
2. `sessions.STATES` is the tuple of S3 states in sort order. `sessions.sort(rows)` sorts by it, then by
   `since`, oldest first, with `None` last. `sessions.FORMAT = "llmpro-sessions.v1"`.
3. `sessions.clean(text, width=None)` removes control characters (C0, C1, and whole ESC
   sequences), joins lines with a space and, when `width` is given, cuts to it. Every string printed from
   a row goes through it (S4).
4. `sessions.ID = re.compile(r'[A-Za-z0-9_-]+')` (opencode ids such as `ses_...` carry an underscore). A record whose id does not fully match is
   `not-readable` (S11).

### Adapters

5. Each adapter module has `CHECKED` (the tool versions it was checked against, as strings) and
   `rows(machine, line=True)` -> list of `Row`. An adapter never raises: an unreadable record is one row
   with state `not-readable` and `error` saying what was wrong, in one plain sentence. With `line=False`
   no transcript or event log is read beyond what the state needs.
6. `sessions._proc(pid)` -> `(uid, program, start_time)` from `/proc/<pid>`, or `None`; the one place that
   reads `/proc/<pid>` for the adapters, so tests can replace it. `program` is the base name of the first
   `cmdline` word, or `comm` only when `cmdline` is empty (Copilot's `comm` is `MainThread`, and so is VS
   Code's server, so `comm` never names a tool). `start_time` is field 22 of `/proc/<pid>/stat`, in clock
   ticks since boot. It never reads `environ`.
7. **Claude Code** (`claude.py`). Every `~/.claude/sessions/*.json` (never `*.key`). Claude does not remove
   the record of a process that died, so each record's process is checked. `pidDomain` is
   `linux:<machine-id>:pid:[<inode>]`: a machine-id that is not empty and differs from `/etc/machine-id`
   skips the record; a pid namespace equal to `/proc/self/ns/pid`, or no `pidDomain`, means `pid` is a pid
   here; another namespace is a session in a container, and `sessions._ns_pid(inode, pid)` finds its pid
   here by scanning `/proc/*/ns/pid` for that namespace and matching the last field of `NSpid` in
   `/proc/<p>/status`; such a row's `where` is `container`. The record is used only when that process is
   live, owned by this user, its program is `claude` (or `node`), and its start time equals `procStart`.
   A record that fails this is skipped, not shown.
   `status` maps by S3; another value is `unknown`. `since` is `statusUpdatedAt`. `line` for
   `needs-reply` is the start of the last assistant text in the transcript, found as
   `~/.claude/projects/*/<sessionId>.jsonl` and read backward from the end (at most the last 256 KB, whole
   lines only); for `needs-approval` it is `permission prompt`. `messagingSocketPath` is never used.
8. **Copilot CLI** (`copilot.py`). Copilot processes are this user's live processes whose program is
   `copilot`. A process has a session open when `~/.copilot/session-state/<id>/inuse.<pid>.hold` exists
   for its pid (Copilot keeps a `flock` on it; `inuse.<pid>.lock` beside it is a plain file, and
   `.session-operation-locks/` holds no lock). One process can hold this in two folders, the one it
   started with and the one it resumed: folders without `events.jsonl` are skipped, and of the rest the
   one whose `events.jsonl` changed last is its session. A process with no such folder yet (a resumed
   session takes one only after its first prompt) has the session named by `--resume <id>` or
   `--resume=<id>` (an id or an id prefix) in its command line; a bare `--resume`, Copilot's own picker,
   gives no row until the first prompt. Command lines are matched, never printed. The folder is
   `context.cwd` of `session.start`. State: read `events.jsonl` backward (at most the last 256 KB, whole
   lines only), skip types in `BOOKKEEPING`, and map the first other type:
   - `permission.requested` is `needs-approval`, with the line from its `intention` field;
   - `assistant.turn_end`, `abort`, `session.error`, `session.resume` and `session.start` are
     `needs-reply`;
   - a type in `WORKING` is `working`;
   - anything else, `session.shutdown` included, is `unknown`.
   `BOOKKEEPING` is `session.usage_checkpoint`, `session.usage_record`, `session.info`,
   `session.warning`, `session.model_change`, `session.permissions_changed`, `session.context_changed`,
   `session.binary_asset`, `session.background_tasks_changed`, `system.notification`, `assistant.idle`
   and any `model.*`. `WORKING` is `user.message`, `assistant.turn_start`, `assistant.message`,
   `tool.execution_start`, `tool.execution_complete`, `permission.completed`, `human_response.recorded`,
   `system.message`, `session.compaction_start`, `session.compaction_complete`, `session.truncation`,
   and any `subagent.*` or `skill.*`. The version is `copilotVersion` from
   `session.start` (read forward, first 64 KB). Do not import `llmpro.sources.copilot_cli`.
9. **opencode** (`opencode.py`). When `tools.which('opencode')` is None, no rows. Every answer comes
   from `opencode api <operation> [--param key=value]`, each run with a 3 second timeout. The server needs
   a credential that `opencode api` supplies; llmpro never calls the server's port itself and never reads
   opencode's database or credential. The sessions listed are:
   - those in `opencode api session.active` (`{"data": {<id>: {"type": "running"}}}`);
   - for each live `opencode` client process of this user (program `opencode`, `serve` not in its command
     line), the session named after `-s` or `--session` in its command line, else the newest session in
     `opencode api session.list` whose `location.directory` is the client's folder (`/proc/<pid>/cwd`).
   For each, `opencode api session.get --param sessionID=<id>` gives the folder (`location.directory`),
   the name (`title`) and `since` (`time.idle`, else `time.updated`, in milliseconds), and
   `opencode api session.permission.list --param sessionID=<id>` (`{"data": [...]}`) gives the state:
   a request is `needs-approval` (line: its `action` and first resource), else `working` when active,
   else `needs-reply`. `pid` is always `None`: the server owns the session and any number of clients may
   share it (Facts), so an opencode session is never open elsewhere in the sense of S7. `where` comes from
   the client process, if any. `version` is `opencode --version`. A failure is one `not-readable` row
   naming the operation and its exit status.
10. `where`: from the process's parents: `vscode` if a parent's program is `code` or `code-insiders`, else
    `ssh` if one is `sshd`, else `terminal`. A container row keeps `container`.
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
- Claude: a record in another pid namespace found through `_ns_pid` (`where` `container`), one with a
  foreign machine-id, one without `pidDomain`.
- Copilot: a process holding two folders (one without `events.jsonl`); a stale hold file of a dead pid;
  a session open with no hold file (only `--resume <id>` in the command line); a bare `--resume`; a
  process whose `comm` is `MainThread` but whose program is not `copilot`.
- opencode: absent; a fake `opencode` in `tools_dir` that answers each `api` operation with recorded
  JSON (active, a client with `-s`, a client found by folder, a permission request); one that exits 1.
- Redaction and ESC sequences in `line`, in `name` and in `folder`; a local-only folder gives no line and
  the transcript is not opened (patch `open` to fail on it); `is_local_project` called once per folder;
  `--no-line`.
- `Row.from_dict` round trip and refusals. Sort order; human output; `--json` shape; `NO_COLOR`; the
  prompt with Enter alone.

## Facts (recorded by the integrator before this wave, S10.1, S10.2, S10.7)

Checked on 2026-10-21 with Claude Code 2.1.305, Copilot CLI 1.0.101 and opencode 2.0.30, with
throwaway sessions in a scratch folder.

- S10.7, Claude: `procStart` is field 22 of `/proc/<pid>/stat` exactly (clock ticks since boot). A
  record whose pid was reused (now another program, another start time) is caught by this check. Records
  of dead processes stay in `~/.claude/sessions/`; on SIGHUP Claude removes its own record. `pidDomain` is
  `linux:<machine-id>:pid:[<inode of the pid namespace>]`. A session in a Docker container that mounts
  `~/.claude` writes its record here with an empty machine-id, its own namespace and its namespace's
  pid; its process is visible here under another pid (`NSpid: <here> <there>`), owned by the user, and
  its start time matches `procStart`. Claude's program is `claude`.
- S10.7, Copilot: the program (first `cmdline` word) is `copilot`; `comm` is `MainThread`, which VS
  Code's server processes also have.
- S10.1, Copilot: `inuse.<pid>.lock` is an 8-byte plain file with no kernel lock. `inuse.<pid>.hold`
  (empty) carries a real `FLOCK ADVISORY WRITE` lock while the process lives. A `copilot --resume`
  process holds hold files in two folders: a placeholder made at start (no `events.jsonl`, or only
  `session.start` and `session.shutdown` when it ended before resuming) and the resumed session. A
  resumed session with no prompt yet has no hold file; a bare `--resume` does not name the session in
  the command line. On SIGHUP Copilot exits within a second, writes `session.shutdown` and removes its
  files. The folder is `context.cwd` in `session.start`. Event types seen after `assistant.turn_end`
  across all local logs are the `BOOKKEEPING` list, plus real work (`tool.*`, `subagent.*`) from
  background tasks.
- S10.2, opencode: a background server (`opencode serve --service`) owns every session; `opencode`,
  `opencode run` and `opencode api` are clients of it, and there is no `attach` command. `opencode api
  session.active` and `opencode api session.permission.list --param sessionID=<id>` print JSON (about
  0.1 second each). `permission.request.list` exists too but answers for one folder only. The server
  needs a credential (a direct HTTP request gets 401); `opencode api` supplies it. A message sent with
  `opencode run -s <id>` while a terminal client has that session open was answered and showed up in the
  open terminal, so two clients share a session and opencode needs no takeover. `session.active` lists
  only sessions that are running; `time.viewed` was not set by a terminal client.
