"""Package 04: `llmpro mcp` serves its own tools and proxies the gitpro MCP child under the `git_` prefix."""
import io
import json
import os
import sys
import time

import pytest

from llmpro import mcp

VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
RO = {'readOnlyHint': True}
SCHEMA = {'type': 'object', 'properties': {'dir': {'type': 'string'}}}


def tool(name, description='A tool.', annotations=None, **extra):
    t = {'name': name, 'description': description, 'inputSchema': SCHEMA}
    if annotations is not None:
        t['annotations'] = annotations
    t.update(extra)
    return t


TOOLS = [
    tool('why', 'Explain the identity.', {'title': 'Why', **RO}),
    tool('scan', 'Scan repos.', RO),
    tool('which_identity', 'Deprecated: use why.', RO),
    tool('setup', 'Changes things.', {'readOnlyHint': False}),
]
RESULT = {'content': [{'type': 'text', 'text': '{"ok": true}'}], 'isError': False, 'extra': 1}


def session(store, *requests):
    """Run the server on these (method, params) pairs; -> {id: response}. Notifications have no id."""
    lines = []
    for i, (method, params) in enumerate(requests, 1):
        msg = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if not method.startswith('notifications/'):
            msg['id'] = i
        lines.append(json.dumps(msg))
    out = io.StringIO()
    assert mcp.serve(str(store), io.StringIO('\n'.join(lines) + '\n'), out) == 0
    return {r['id']: r for r in map(json.loads, out.getvalue().splitlines())}


def init(version='2025-06-18'):
    return ('initialize', {'protocolVersion': version, 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '0'}})


def names(resp):
    return [t['name'] for t in resp['result']['tools']]


def mcp_calls(fg):
    return [c for c in fg.calls() if c[:1] == ['mcp']]


@pytest.mark.parametrize('version', VERSIONS)
def test_initialize_echoes_known_version(store, fake_gitpro, version):
    fake_gitpro.mcp(TOOLS)
    r = session(store, init(version))[1]['result']
    assert r['protocolVersion'] == version
    assert r['serverInfo']['name'] == 'llmpro'
    assert r['capabilities'] == {'tools': {'listChanged': False}}
    assert 'read-only' in r['instructions']
    assert mcp_calls(fake_gitpro) == []  # the child is not started at initialize


def test_initialize_unknown_version_gets_newest(store, fake_gitpro):
    fake_gitpro.mcp(TOOLS)
    assert session(store, init('2099-01-01'))[1]['result']['protocolVersion'] == '2025-11-25'
    assert mcp.PROTOCOL_VERSIONS == VERSIONS
    assert mcp_calls(fake_gitpro) == []


def test_tools_list(store, fake_gitpro):
    fake_gitpro.mcp(TOOLS)
    resp = session(store, init(), ('tools/list', {}))[2]
    assert names(resp) == ['find', 'check', 'sync_status', 'sessions', 'git_why', 'git_scan']
    by = {t['name']: t for t in resp['result']['tools']}
    assert all(t['annotations']['readOnlyHint'] for t in by.values())
    assert by['git_why']['annotations'] == {'title': 'git: Why', 'readOnlyHint': True}
    assert by['git_why']['description'] == 'Explain the identity.'
    assert by['git_why']['inputSchema'] == SCHEMA
    assert by['git_scan']['annotations'] == RO  # no title, none added


def test_name_clash_and_deprecated_case(store, fake_gitpro):
    fake_gitpro.mcp([tool('why', annotations=RO), tool('why', annotations=RO), tool('old', 'DEPRECATED soon', RO),
                     tool('noann'), tool('sync_status', annotations=RO)])
    resp = session(store, init(), ('tools/list', {}))[2]
    assert names(resp) == ['find', 'check', 'sync_status', 'sessions', 'git_why', 'git_sync_status']


def test_call_returns_child_result_unchanged(store, fake_gitpro):
    fake_gitpro.mcp(TOOLS, results={'why': RESULT})
    resp = session(store, init(), ('tools/call', {'name': 'git_why', 'arguments': {'dir': '.'}}))[2]
    assert resp['result'] == RESULT
    assert mcp_calls(fake_gitpro) == [['mcp']]


def test_child_never_gets_allow_execution(store, fake_gitpro, monkeypatch):
    from llmpro import plugins
    from llmpro.plugins import git
    fake_gitpro.mcp(TOOLS, results={'why': RESULT})
    monkeypatch.setattr(git.plugin, 'mcp_child', lambda ctx: [fake_gitpro.path, 'mcp', '--allow-execution'])
    plugins.reset_cache()
    resp = session(store, init(), ('tools/call', {'name': 'git_why', 'arguments': {}}))[2]
    assert resp['result'] == RESULT
    assert mcp_calls(fake_gitpro) == [['mcp']]
    assert all('--allow-execution' not in c for c in fake_gitpro.calls())


def test_unknown_tool(store, fake_gitpro):
    fake_gitpro.mcp(TOOLS)
    resp = session(store, init(), ('tools/call', {'name': 'git_nope', 'arguments': {}}),
                   ('tools/call', {'name': 'nope', 'arguments': {}}))
    assert resp[2]['error']['code'] == -32602
    assert resp[3]['error']['code'] == -32602


def write_child(fg, body):
    """Replace the fake gitpro with a small MCP server of our own. It answers --version --json like the fake."""
    version = json.dumps(json.loads(open(os.path.join(os.path.dirname(fg.path), 'gitpro.json')).read())['version'])
    text = f'''#!{sys.executable}
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, 'gitpro.log'), 'a') as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:] == ['--version', '--json']:
    print({version!r})
    sys.exit(0)
TOOLS = json.loads({json.dumps(json.dumps(TOOLS))})
def send(i, **kw):
    sys.stdout.write(json.dumps(dict(jsonrpc='2.0', id=i, **kw)) + "\\n"); sys.stdout.flush()
{body}
'''
    with open(fg.path, 'w') as fh:
        fh.write(text)


LOOP = '''
for line in sys.stdin:
    msg = json.loads(line)
    if 'id' not in msg:
        continue
    m, p = msg['method'], msg.get('params') or {}
    if m == 'initialize':
        send(msg['id'], result={'protocolVersion': p['protocolVersion'], 'capabilities': {}, 'serverInfo': {}})
        %s
    elif m == 'tools/list':
        send(msg['id'], result={'tools': TOOLS})
    elif m == 'tools/call':
        %s
'''


def test_child_error_becomes_tool_error(store, fake_gitpro):
    write_child(fake_gitpro, LOOP % ('pass', "send(msg['id'], error={'code': -32000, 'message': 'boom'})"))
    resp = session(store, init(), ('tools/call', {'name': 'git_why', 'arguments': {}}))[2]
    assert resp['result']['isError'] is True
    assert 'boom' in resp['result']['content'][0]['text']


def test_child_dies_then_restarts(store, fake_gitpro, tmp_path):
    marker = tmp_path / 'died'
    die = f"""
        if not os.path.exists({str(marker)!r}):
            open({str(marker)!r}, 'w').close()
            sys.exit(0)"""
    write_child(fake_gitpro, LOOP % (die, "send(msg['id'], result={'content': [{'type': 'text', 'text': 'hi'}]})"))
    resp = session(store, init(), ('tools/call', {'name': 'git_why', 'arguments': {}}),
                   ('tools/call', {'name': 'git_why', 'arguments': {}}))
    assert resp[2]['result']['isError'] is True
    assert resp[3]['result'] == {'content': [{'type': 'text', 'text': 'hi'}]}
    assert len(mcp_calls(fake_gitpro)) == 2


def test_child_gives_up_after_three_restarts(store, fake_gitpro):
    write_child(fake_gitpro, LOOP % ('sys.exit(0)', 'pass'))
    reqs = [init()] + [('tools/call', {'name': 'git_why', 'arguments': {}})] * 6
    resp = session(store, *reqs)
    assert len(mcp_calls(fake_gitpro)) == 4  # one start and three restarts
    for i in range(2, 8):
        assert resp[i]['result']['isError'] is True
    assert 'unavailable' in resp[7]['result']['content'][0]['text']


def test_child_timeout(store, fake_gitpro, monkeypatch):
    monkeypatch.setenv('LLMPRO_MCP_CHILD_TIMEOUT', '0.5')
    write_child(fake_gitpro, LOOP % ('pass', 'time.sleep(30)'))
    start = time.time()
    resp = session(store, init(), ('tools/call', {'name': 'git_why', 'arguments': {}}))[2]
    assert time.time() - start < 10
    assert resp['result']['isError'] is True
    assert 'timed out' in resp['result']['content'][0]['text']


def test_child_garbage(store, fake_gitpro):
    write_child(fake_gitpro, LOOP % ('pass', "sys.stdout.write('not json\\n'); sys.stdout.flush()"))
    resp = session(store, init(), ('tools/call', {'name': 'git_why', 'arguments': {}}))[2]
    assert resp['result']['isError'] is True


def test_gitpro_missing(store):
    resp = session(store, init(), ('tools/list', {}))
    assert names(resp[2]) == ['find', 'check', 'sync_status', 'sessions']
    text = resp[1]['result']['instructions']
    assert 'gitpro' in text and 'git' in text
    assert 'not installed' in text


def test_gitpro_without_mcp_lists_no_git_tools(store, fake_gitpro):
    fake_gitpro.version(capabilities=('why',))
    assert names(session(store, init(), ('tools/list', {}))[2]) == ['find', 'check', 'sync_status', 'sessions']


def test_child_that_will_not_start_is_skipped(store, fake_gitpro):
    write_child(fake_gitpro, 'sys.exit(1)')
    assert names(session(store, init(), ('tools/list', {}))[2]) == ['find', 'check', 'sync_status', 'sessions']


def test_empty_lists_and_ping(store, fake_gitpro):
    resp = session(store, init(), ('resources/list', {}), ('prompts/list', {}), ('ping', {}))
    assert resp[2]['result'] == {'resources': []}
    assert resp[3]['result'] == {'prompts': []}
    assert resp[4]['result'] == {}
    assert mcp_calls(fake_gitpro) == []


def test_sync_status_unchanged_without_child(store):
    resp = session(store, init(), ('tools/call', {'name': 'sync_status', 'arguments': {}}))[2]
    assert resp['result']['isError'] is False


def test_cancel_is_forwarded(store, fake_gitpro, tmp_path):
    seen = tmp_path / 'cancelled'
    body = LOOP % ('pass', "send(msg['id'], result={'content': []})")
    body = body.replace("    if 'id' not in msg:\n        continue",
                        f"    if 'id' not in msg:\n        open({str(seen)!r}, 'a').write(msg['method'])\n        continue")
    write_child(fake_gitpro, body)
    from llmpro.mcp_proxy import Child
    c = Child('git', [fake_gitpro.path, 'mcp'], '2025-06-18')
    try:
        assert len(c.tools()) == 4
        c.notify('notifications/cancelled', {'requestId': 99})
        deadline = time.time() + 5
        while time.time() < deadline and 'cancelled' not in (seen.read_text() if seen.exists() else ''):
            time.sleep(0.05)
        assert 'notifications/cancelled' in seen.read_text()
    finally:
        c.close()


def test_shutdown_closes_child(store, fake_gitpro, tmp_path):
    ended = tmp_path / 'ended'
    body = LOOP % ('pass', "send(msg['id'], result={'content': []})") + f"\nopen({str(ended)!r}, 'w').close()\n"
    write_child(fake_gitpro, body)
    session(store, init(), ('tools/call', {'name': 'git_why', 'arguments': {}}))
    deadline = time.time() + 5
    while time.time() < deadline and not ended.exists():
        time.sleep(0.05)
    assert ended.exists()  # stdin was closed, so the child left its loop on its own
