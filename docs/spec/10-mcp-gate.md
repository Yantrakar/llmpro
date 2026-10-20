# 10 MCP never returns local content

Wave 5. Branch `spec/10-mcp-gate`. Test file `tests/test_mcp_gate.py`.

## Goal

Today the MCP `find` tool returns hits from `local/history/`, so the chats of a local-only project can
reach an assistant and through it a cloud model. Make the MCP server unable to return anything from
`local/` or `sessions/` (DECISIONS H2). `llmpro find` at the terminal keeps searching `local/history/`.

## May edit

`src/llmpro/find.py`, `src/llmpro/mcp.py`, `src/llmpro/cli.py` (the `find` parser and its call only).

## Creates

`tests/test_mcp_gate.py`.

## Uses

`find.candidates(store, machine, tool, since, want_history, want_memory)` with its loop
`for base in ('history', os.path.join('local', 'history'))`; `find.search(...)`, `find.run(...)`;
`mcp.command(name, args)`, which builds `['find', '--json', ..., '--', *query]`; `mcp.call(store, name,
args)`, which runs `[sys.executable, '-m', 'llmpro', '--store', store] + cmd` and returns the MCP result.

## Requirements

1. `llmpro find --shared-only`: search only what can leave the machine: `history/` and `memory/`, never
   `local/`. Pass it down as `search(..., shared_only=False)` and `candidates(..., shared_only=False)`.
   Help text: "leave out local-only chats (the MCP tool always does)".
2. `mcp.command('find', ...)` always adds `--shared-only`. No tool argument can turn it off.
3. A last gate in `mcp.py`, applied to the `find` result before it is returned: parse the JSON; drop
   every hit whose `path` is absolute, contains `..`, or whose first part is `local` or `sessions`; if
   the output does not parse as the expected JSON, return an MCP error result ("find output could not be
   checked") instead of the raw text. Count dropped hits; if any were dropped, add
   `"withheld": N` to the returned JSON. This gate must not depend on item 2: a test feeds it a result
   with a `local/...` hit directly.
4. The proxied `git_*` tools are not touched.
5. `llmpro find` without the flag behaves as today, and its `--json` keys stay.
6. Update the `find` module docstring and the `mcp` module docstring in one line each.

## Tests (`tests/test_mcp_gate.py`)

- A store with one matching chat in `history/` and one in `local/history/`: `llmpro find word --json`
  returns both; with `--shared-only` only the first.
- Through `mcp.handle` (a `tools/call` for `find`): only the `history/` hit, never a `local/` path, and
  no `withheld` key.
- The gate alone, given JSON with a `local/history/...` hit, a `sessions/...` hit, an absolute path and a
  `history/...` hit: keeps only the last, `withheld` is 3.
- The gate given text that is not JSON: an error result, and the text is not in it.
- `mcp.command('find', {'query': ['x'], ...})` contains `--shared-only` whatever the arguments.
