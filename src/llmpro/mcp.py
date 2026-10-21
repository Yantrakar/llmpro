"""`llmpro mcp`: a stdio MCP server (JSON-RPC 2.0, one message per line). Standard library only.

Tools: find, check (read-only, never --fix) and sync_status (sync -n). Each runs the command of the same name in
a child process with --json and returns its output as text, so the terminal and the assistants see the same thing.
When something needs doing, the output says which command the person runs.
The find tool always passes --shared-only and a last gate drops any hit under local/ or sessions/.
"""
import json
import os
import subprocess
import sys

from .mcp_proxy import PROTOCOL_VERSIONS, Proxy

INSTRUCTIONS = ('llmpro offers tools to search past chats and memories in the store, to check the store for '
                'secrets, to see what a sync would do, and, when gitpro is installed, the git identity tools '
                'with the prefix git_. Every tool is read-only. When something needs changing, the tool says '
                'which command to run, and the person runs it.')
OWN = ('find', 'check', 'sync_status', 'sessions')
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
    {'name': 'sessions',
     'description': 'List the open assistant sessions on this machine and on the machines in config.toml: tool, '
                    'machine, name, folder, state (needs-approval, needs-reply, working, unknown, not-readable) '
                    'and time. Read-only: no content line and no session in a local-only folder. To continue a '
                    'session the person runs `llmpro resume`.',
     'inputSchema': {'type': 'object', 'properties': {}},
     'annotations': {'title': 'Open sessions', **READ_ONLY}},
]


def command(name, args):
    """-> the llmpro arguments for a tool call; ValueError if the arguments are wrong."""
    if name == 'check':
        return ['check', '--json']
    if name == 'sync_status':
        return ['sync', '-n', '--json']
    if name == 'sessions':
        return ['resume', '--json', '--no-line']
    if name == 'find':
        query = args.get('query')
        if not isinstance(query, list) or not query or not all(isinstance(w, str) and w for w in query):
            raise ValueError('query must be a non-empty list of words')
        cmd = ['find', '--json', '--shared-only']
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


def unsafe(path):
    """True for a hit path that is not a plain path inside the store, or that is under local/ or sessions/."""
    if not isinstance(path, str) or os.path.isabs(path) or path.startswith(('/', '\\')):
        return True
    parts = path.replace('\\', '/').split('/')
    return '..' in parts or parts[0] in ('local', 'sessions')


def refuse(text):
    return {'content': [{'type': 'text', 'text': text}], 'isError': True}


def gate_find(result):
    """-> the find result without hits from local/, sessions/ or outside the store; an error if it cannot be checked."""
    try:
        body = json.loads(result['content'][0]['text'])
        hits = body['results']
        if not isinstance(body, dict) or not isinstance(hits, list):
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError):
        return refuse('find output could not be checked')
    kept = [h for h in hits if isinstance(h, dict) and not unsafe(h.get('path'))]
    if len(kept) != len(hits):
        body['results'] = kept
        body['withheld'] = len(hits) - len(kept)
    return {'content': [{'type': 'text', 'text': json.dumps(body, ensure_ascii=False)}], 'isError': False}


def gate_sessions(result):
    """-> the sessions result with every `line` emptied and every local-only row removed; an error if it cannot
    be checked. A row that does not say local_only is false is removed too."""
    try:
        body = json.loads(result['content'][0]['text'])
        rows = body['rows']
        if not isinstance(body, dict) or not isinstance(rows, list):
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError):
        return refuse('sessions output could not be checked')
    kept = [dict(r, line='') for r in rows if isinstance(r, dict) and r.get('local_only') is False]
    if len(kept) != len(rows):
        body['withheld'] = len(rows) - len(kept)
    body['rows'] = kept
    return {'content': [{'type': 'text', 'text': json.dumps(body, ensure_ascii=False)}], 'isError': False}


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
        result = {'content': [{'type': 'text', 'text': r.stdout.strip()}], 'isError': False}
        if name == 'find':
            return gate_find(result)
        return gate_sessions(result) if name == 'sessions' else result
    text = (r.stderr or r.stdout).strip() or f'exit {r.returncode}'
    return {'content': [{'type': 'text', 'text': text}], 'isError': True}


def instructions(proxy):
    return ' '.join([INSTRUCTIONS] + proxy.missing)


def handle(store, msg, proxy=None):
    """-> the response dict for a request, or None for a notification."""
    if proxy is None:
        proxy = Proxy(store)
        try:
            return handle(store, msg, proxy)
        finally:
            proxy.close()
    mid = msg.get('id')
    method = msg.get('method')
    if mid is None:
        if method == 'notifications/cancelled' and isinstance(msg.get('params'), dict):
            proxy.cancel(msg['params'])
        return None  # notifications (initialized, cancelled) need no answer

    def reply(result):
        return {'jsonrpc': '2.0', 'id': mid, 'result': result}

    def error(code, text):
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': code, 'message': text}}

    params = msg.get('params')
    params = params if isinstance(params, dict) else {}
    if method == 'initialize':
        from . import __version__
        asked = params.get('protocolVersion')
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1]
        proxy.set_protocol(version)
        return reply({'protocolVersion': version, 'capabilities': {'tools': {'listChanged': False}},
                      'serverInfo': {'name': 'llmpro', 'version': __version__},
                      'instructions': instructions(proxy)})
    if method == 'ping':
        return reply({})
    if method == 'resources/list':
        return reply({'resources': []})
    if method == 'prompts/list':
        return reply({'prompts': []})
    if method == 'tools/list':
        return reply({'tools': TOOLS + proxy.list_tools(OWN)})
    if method == 'tools/call':
        if not isinstance(params.get('arguments') or {}, dict):
            return error(-32602, 'invalid params')
        name = params.get('name')
        try:
            if isinstance(name, str) and name not in OWN and proxy.owns(name):
                return reply(proxy.call(OWN, name, params.get('arguments') or {}))
            return reply(call(store, name, params.get('arguments')))
        except KeyError:
            return error(-32602, f'unknown tool: {name}')
    return error(-32601, f'method not found: {method}')


def serve(store, stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    proxy = Proxy(store)
    try:
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
                    resp = handle(store, msg, proxy)
            if resp is not None:
                stdout.write(json.dumps(resp, ensure_ascii=False) + '\n')
                stdout.flush()
    finally:
        proxy.close()
    return 0
