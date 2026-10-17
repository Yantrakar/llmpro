#!/usr/bin/env python3
"""A fake `gitpro` for tests. One file. It reads its answers from `gitpro.json` next to itself and appends
each argv to `gitpro.log` there. The fixture `fake_gitpro` copies it to a temporary folder as `gitpro`.

gitpro.json: {"version": {...}, "answers": [{"argv": [...], "stdout": "...", "exit": 0, "sleep": 0}],
              "mcp": {"tools": [...], "results": {...}, "protocols": [...]}}
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load():
    try:
        with open(os.path.join(HERE, 'gitpro.json'), encoding='utf-8') as fh:
            return json.load(fh)
    except OSError:
        return {}


def log(argv):
    with open(os.path.join(HERE, 'gitpro.log'), 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(argv) + '\n')


def reply(out, obj):
    out.write(json.dumps(obj) + '\n')
    out.flush()


def mcp(spec):
    protocols = spec.get('protocols') or ['2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25']
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict) or 'id' not in msg:  # a notification
            continue
        method, params = msg.get('method'), msg.get('params') or {}
        if method == 'initialize':
            asked = params.get('protocolVersion')
            result = {'protocolVersion': asked if asked in protocols else protocols[-1],
                      'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fake-gitpro', 'version': '0'}}
        elif method == 'tools/list':
            result = {'tools': spec.get('tools', [])}
        elif method == 'tools/call':
            name = params.get('name')
            result = (spec.get('results') or {}).get(name) or {
                'isError': True, 'content': [{'type': 'text', 'text': f'fake gitpro: no result for {name}'}]}
        else:
            reply(sys.stdout, {'jsonrpc': '2.0', 'id': msg['id'], 'error': {'code': -32601, 'message': 'no such method'}})
            continue
        reply(sys.stdout, {'jsonrpc': '2.0', 'id': msg['id'], 'result': result})
    return 0


def main(argv):
    log(argv)
    data = load()
    if argv == ['--version', '--json'] and 'version' in data:
        time.sleep(data.get('version_sleep', 0))
        v = data['version']
        if isinstance(v, str):  # printed as it is, to test an answer that is not JSON
            print(v)
        else:
            print(json.dumps(v))
        return 0
    if argv[:1] == ['mcp'] and 'mcp' in data:
        return mcp(data['mcp'])
    for a in data.get('answers', []):
        if a['argv'] == argv:
            time.sleep(a.get('sleep', 0))
            out = a.get('stdout', '')
            sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
            sys.stdout.write('\n')
            return a.get('exit', 0)
    print(f'fake gitpro: no answer for {argv}', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
