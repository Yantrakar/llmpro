# 04 One MCP server

Wave 3. Branch `spec/04-mcp`. Test file `tests/test_mcp_proxy.py`.

## Goal

`llmpro mcp` is the one MCP server an assistant needs (DECISIONS I4). It serves llmpro's own tools and,
for every plugin that offers an MCP child (today: `git`, which offers `gitpro mcp`), that child's
current tools under the plugin's prefix. Everything stays read-only.

## May edit

`src/llmpro/mcp.py`.

## Creates

`src/llmpro/mcp_proxy.py`, `tests/test_mcp_proxy.py`.

## Uses (from 03)

```python
from llmpro import plugins            # plugins.load() -> list[Plugin]
plugin.name                           # 'git'
plugin.mcp_child(ctx) -> list[str] | None   # e.g. ['/path/gitpro', 'mcp']
from llmpro.plugins.base import Context     # Context(store, home)
```

Tests use the `fake_gitpro` fixture: `fg.version(...)`, `fg.mcp(tools, results, protocols)`,
`fg.calls()`.

## Requirements

1. **Protocol versions.** `PROTOCOL_VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')`,
   newest last. `initialize` answers with the client's `protocolVersion` when it is in the list, else
   the newest. serverInfo `{"name": "llmpro", "version": __version__}`. Add `instructions`: one short
   paragraph saying what the server offers (search past chats and memories, check the store for
   secrets, sync status, and the git identity tools when gitpro is installed), that every tool is
   read-only, and that the person runs the commands the tools suggest.
2. **Own tools** stay as they are (`find`, `check`, `sync_status`), with `readOnlyHint: true`.
3. **Children.** `mcp_proxy.Child(name, argv, protocol)` starts the child on first need (the first
   `tools/list` or a call of a prefixed tool), not at `initialize`, so a slow or broken child never
   delays the handshake. It sends `initialize` with the protocol the client agreed (or the newest the
   child offers, if it answers with another one it supports), then `notifications/initialized`, then
   `tools/list`. Messages are one JSON object per line on the child's stdin and stdout; the child's
   stderr goes to llmpro's stderr. Each request to the child waits at most 30 s
   (`LLMPRO_MCP_CHILD_TIMEOUT` overrides, in seconds). The child is never started with
   `--allow-execution`, and llmpro removes that argument if a plugin passes it.
4. **Listing.** For each child tool: skip it when its description starts with `Deprecated` (any case),
   when its annotations have `readOnlyHint` false, or when its name already exists after prefixing.
   Otherwise list it as `<plugin>_<name>` (`git_why`), with the child's description, input schema and
   annotations unchanged except `title`, which gets the prefix `git: ` when present.
5. **Calls.** `tools/call` of `git_why` sends `tools/call` `why` with the same arguments to the child and
   returns the child's `result` unchanged. A child `error` becomes a tool result with `isError: true`
   and the error message as text. A child that died, timed out, or answered garbage: a tool result with
   `isError: true` and one line saying what happened; the child is restarted on the next call, at most
   three times per server run, after which the plugin's tools answer "unavailable" without retrying.
6. **No child.** A plugin whose `mcp_child` is None, or whose child fails to start: its tools are not
   listed and `sync_status` is not changed. The server's `instructions` (built at `initialize`) end with one sentence per plugin that is missing, from the
   plugin's `status().reason` and `hint` (for example, the git identity tools need gitpro).
7. **Requests llmpro does not handle** (`resources/list`, `prompts/list`): answer with empty lists
   (`{"resources": []}`, `{"prompts": []}`) and advertise only `tools` in capabilities. `ping` answers
   `{}`. `notifications/cancelled` for a proxied request is forwarded to the child.
8. **Shutdown.** When stdin closes, close each child's stdin, wait up to 2 s, then kill it.
9. The server stays single-threaded and standard library only; reading the child's reply may use
   `selectors` or a reader thread, your choice, as long as the timeout in item 3 holds.

## Tests (`tests/test_mcp_proxy.py`)

Drive `mcp.serve(store, stdin, stdout)` with `io.StringIO` lines, or run `python -m llmpro mcp` as a
child with pipes; both are fine.

- `initialize` with each listed version answers with it; with `2099-01-01` answers `2025-11-25`; the
  fake gitpro has not been started yet (`fg.calls()` has no `['mcp']`).
- With the fake offering `why`, `scan`, `which_identity` (description starting "Deprecated") and a tool
  with `readOnlyHint: false`: `tools/list` lists `find`, `check`, `sync_status`, `git_why`, `git_scan`
  and nothing else.
- `tools/call git_why {"dir": "."}` returns the fake's result unchanged, and the fake saw `why` with the
  same arguments.
- A fake that answers a tool with a JSON-RPC error gives `isError: true` with the message.
- A fake that exits after `initialize`: the call gives `isError: true`; the next call restarts it.
- gitpro missing: `tools/list` has only the three own tools, and the `initialize` instructions mention
  that the git tools need gitpro.
- The child is never started with `--allow-execution` (check `fg.calls()`).
- `resources/list` and `prompts/list` answer empty lists.

## Done when

README section 2 item 12 holds.
