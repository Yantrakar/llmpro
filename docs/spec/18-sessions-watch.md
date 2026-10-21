# 18 Watch the sessions

Wave 12. Branch `spec/18-sessions-watch`. Test file `tests/test_sessions_watch.py`.

## Goal

`llmpro resume --watch` keeps the list current in the terminal and sends a desktop notice when a session
starts waiting for the user (S1, S4, S5).

## May edit

`src/llmpro/sessions/__init__.py`, `src/llmpro/sessions/remote.py`, `src/llmpro/cli.py`,
`src/llmpro/store.py`, `src/llmpro/templates/config.toml`, `src/llmpro/sync.py` (only to make `notify`
call the new module).

## Creates

`src/llmpro/notice.py`, `src/llmpro/sessions/watch.py`, `tests/test_sessions_watch.py`.

## Uses

From 15 to 17: `sessions.all_rows(machine=None, control=None, line=True)`, `sessions.resolve`,
`sessions.clean`, `launch.run(row)`, `launch.remote_argv(m, target)`, `store.machines(store)`,
`remote.command(m, args)`, `remote.ask(m, timeout=10, control=None)`. `sync.notify(msg)`, `sync.Hub` (as
the model for the shared login). `tools.which('ssh')`, `tools.which('notify-send')`.

## Requirements

1. `notice.notify(title, msg, icon=None)` sends a desktop notice through `notify-send` when it is found,
   and does nothing otherwise. `sync.notify(msg)` keeps its behavior by calling it with the title
   `llmpro sync failed` and the warning icon.
2. `--watch` needs a terminal; without one it exits 2 saying so. `--json --watch` prints one JSON line per
   change instead (the 15 envelope each time), for scripts; this form works without a terminal.
3. The screen is redrawn every `[sessions] refresh` seconds (default 5, at least 2): clear, the list as in
   15, the time of the last refresh, per machine errors, and a last line `number or name + Enter to
   continue, q + Enter to quit`. Input is read a line at a time with `select`, so no raw mode and no
   curses. Ctrl-C quits with 130.
4. A machine still being asked is not asked again; its last rows are shown, marked `(as of <time>)`.
5. At the start it opens one ssh login per machine, one after another (each may ask a password on the
   terminal), with ControlMaster and a ControlPath in a fresh `tempfile.mkdtemp()` folder, as `sync.Hub`
   does. A machine whose login fails is an error row and is not asked again. Every refresh passes the
   ControlPath to `remote.ask` and keeps `BatchMode=yes`. The logins are closed (`-O exit`) and the folder
   removed when watch ends, however it ends.
6. A notice is sent when a row enters `needs-approval` or `needs-reply` from another state, or appears in
   one of them, but not for the rows present at the start. Its title is `llmpro`; its text is
   `<tool> <name or id> on <machine>: needs a reply` (or `needs approval`), cleaned, never the `line`. For a
   local-only row it is `<tool> on <machine>: needs a reply`. Notices for one row are at least 60 seconds
   apart. `[sessions] notify = false` turns them off.
7. Choosing a row stops the redraw, runs `launch.run` (or `launch.remote_argv` as a child) with the
   terminal to itself, and returns to the watch when it ends.
8. The template gains a `[sessions]` table with `refresh = 5` and `notify = true`, each with a one-line
   comment. `store.sessions_config(store)` reads it from the store's `config.toml` and fills in defaults;
   a wrong type is replaced by the default with one warning. Package 19 adds its keys to the same table.
9. `resume.watch` is added to `CAPABILITIES`.

## Tests

A fake clock and a fake row source: notices on the right transitions only, none for rows present at the
start, the 60 second gap, `notify = false`, no name for a local-only row. A slow machine is not asked
twice and its rows are marked. The ssh masters are opened one after another and closed (the fake `ssh`
records `-O exit`) even when the loop raises. `--json --watch` prints one envelope per change. No
terminal: exit 2. `sync.notify` still sends its old title.
