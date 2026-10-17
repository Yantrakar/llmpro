"""`llmpro mcp`: a stdio MCP server (JSON-RPC 2.0, one message per line). Standard library only.

Tools: find, check (read-only, never --fix) and sync_status (sync -n). Each runs the command of the same name in
a child process with --json and returns its output as text, so the terminal and the assistants see the same thing.
When something needs doing, the output says which command the person runs.
"""
import json
import subprocess
import sys

PROTOCOL = '2025-06-18'
READ_ONLY = {'readOnlyHint': True}

TOOLS = [
    {'name': 'find',
     'description': 'Search past conversations and memories in the store. Words are ANDed, case-insensitive. '
                    'Returns a JSON list: kind, date, machine, tool, scope, title, path, summary.',
     'inputSchema': {'type': 'object', 'properties': {
         'query': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'description': 'words to find'},
         'machine': {'type': 'string'}, 'tool': {'type': 'string'},
         'since': {'type': 'string', 'description': 'YYYY-MM-DD'},
         'scope': {'type': 'string', 'enum': ['all', 'memory', 'history'], 'description': 'default all'},
         'verbose': {'type': 'boolean', 'description': 'add up to 3 matching lines per file'}},
         'required': ['query']},
     'annotations': {'title': 'Find in the store', **READ_ONLY}},
    {'name': 'check',
     'description': 'Check the store for secrets and broken files, read-only. Returns JSON: passed, exit, output. '
                    'To fix the memory index the person runs `llmpro check --fix`.',
     'inputSchema': {'type': 'object', 'properties': {}},
     'annotations': {'title': 'Check the store', **READ_ONLY}},
    {'name': 'sync_status',
     'description': 'What `llmpro sync` would do (sync -n): new sessions, commits ahead and behind, missing '
                    'git settings. Changes nothing. To sync the person runs `llmpro sync`.',
     'inputSchema': {'type': 'object', 'properties': {}},
     'annotations': {'title': 'Sync status', **READ_ONLY}},
]


def command(name, args):
    """-> the llmpro arguments for a tool call; ValueError if the arguments are wrong."""
    if name == 'check':
        return ['check', '--json']
    if name == 'sync_status':
        return ['sync', '-n', '--json']
    if name == 'find':
        query = args.get('query')
        if not isinstance(query, list) or not query or not all(isinstance(w, str) and w for w in query):
            raise ValueError('query must be a non-empty list of words')
        cmd = ['find', '--json']
        for key in ('machine', 'tool', 'since'):
            if args.get(key):
                cmd += [f'--{key}', str(args[key])]
        scope = args.get('scope', 'all')
        if scope not in ('all', 'memory', 'history'):
            raise ValueError('scope must be all, memory or history')
        if scope != 'all':
            cmd.append(f'--{scope}')
        if args.get('verbose'):
            cmd.append('-v')
        return cmd + ['--'] + query
    raise KeyError(name)


def call(store, name, args):
    """-> the result of tools/call."""
    try:
        cmd = command(name, args or {})
    except KeyError:
        raise
    except ValueError as exc:
        return {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
    r = subprocess.run([sys.executable, '-m', 'llmpro', '--store', store] + cmd,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    # 0 ok and 1 "problems or nothing found" are answers; 2 is an error
    if r.returncode in (0, 1):
        return {'content': [{'type': 'text', 'text': r.stdout.strip()}], 'isError': False}
    text = (r.stderr or r.stdout).strip() or f'exit {r.returncode}'
    return {'content': [{'type': 'text', 'text': text}], 'isError': True}


def handle(store, msg):
    """-> the response dict for a request, or None for a notification."""
    mid = msg.get('id')
    method = msg.get('method')
    if mid is None:
        return None  # notifications (initialized, cancelled) need no answer

    def reply(result):
        return {'jsonrpc': '2.0', 'id': mid, 'result': result}

    def error(code, text):
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': code, 'message': text}}

    if method == 'initialize':
        from . import __version__
        return reply({'protocolVersion': PROTOCOL, 'capabilities': {'tools': {'listChanged': False}},
                      'serverInfo': {'name': 'llmpro', 'version': __version__}})
    if method == 'ping':
        return reply({})
    if method == 'tools/list':
        return reply({'tools': TOOLS})
    if method == 'tools/call':
        params = msg.get('params') or {}
        if not isinstance(params, dict) or not isinstance(params.get('arguments') or {}, dict):
            return error(-32602, 'invalid params')
        try:
            return reply(call(store, params.get('name'), params.get('arguments')))
        except KeyError:
            return error(-32602, f"unknown tool: {params.get('name')}")
    return error(-32601, f'method not found: {method}')


def serve(store, stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}}
        else:
            if not isinstance(msg, dict):
                resp = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'invalid request'}}
            else:
                resp = handle(store, msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + '\n')
            stdout.flush()
    return 0
