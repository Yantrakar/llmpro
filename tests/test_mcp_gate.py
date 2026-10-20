"""The MCP server never returns local content: find --shared-only and the last gate in mcp.py."""
import json
import os

import pytest

from llmpro import cli, mcp


def put(store, rel, text):
    p = os.path.join(store, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(text)


def chat(title):
    return f'---\nid: codex:abc\ntool: codex\nmachine: m1\ntitle: {title}\n---\n### user\nthe uniqueword here\n'


def wrap(text):
    return {'content': [{'type': 'text', 'text': text}], 'isError': False}


@pytest.fixture
def filled(store):
    put(store, 'history/m1/codex/2026/2026-09-10_shared_aaaa1111.md', chat('Shared chat'))
    put(store, 'local/history/m1/codex/2026/2026-10-07_loc_dddd4444.md', chat('Local chat'))
    return store


def hits_of(capsys):
    return json.loads(capsys.readouterr().out)['results']


def test_find_cli_shared_only(filled, capsys):
    assert cli.main(['find', '--store', filled, 'uniqueword', '--json']) == 0
    assert len(hits_of(capsys)) == 2
    assert cli.main(['find', '--store', filled, '--shared-only', 'uniqueword', '--json']) == 0
    assert [h['title'] for h in hits_of(capsys)] == ['Shared chat']


def test_mcp_find_returns_no_local_hit(filled):
    msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
           'params': {'name': 'find', 'arguments': {'query': ['uniqueword']}}}
    r = mcp.handle(filled, msg)['result']
    assert r['isError'] is False
    body = json.loads(r['content'][0]['text'])
    assert [h['title'] for h in body['results']] == ['Shared chat']
    assert 'withheld' not in body
    assert 'local/' not in r['content'][0]['text']


def test_gate_keeps_only_safe_hits():
    hits = [{'path': 'local/history/m/t/2026/x.md'}, {'path': 'sessions/codex/raw.md'},
            {'path': '/etc/passwd'}, {'path': 'history/m/t/2026/ok.md'}]
    r = mcp.gate_find(wrap(json.dumps({'ok': True, 'command': 'find', 'results': hits})))
    assert r['isError'] is False
    body = json.loads(r['content'][0]['text'])
    assert [h['path'] for h in body['results']] == ['history/m/t/2026/ok.md']
    assert body['withheld'] == 3


def test_gate_drops_dotdot_paths():
    text = json.dumps({'ok': True, 'command': 'find', 'results': [{'path': 'history/../local/history/a.md'}]})
    body = json.loads(mcp.gate_find(wrap(text))['content'][0]['text'])
    assert body['results'] == [] and body['withheld'] == 1


def test_gate_refuses_text_that_is_not_json():
    r = mcp.gate_find(wrap('secret local words'))
    assert r['isError'] is True
    assert 'find output could not be checked' in r['content'][0]['text']
    assert 'secret local words' not in r['content'][0]['text']


def test_command_always_adds_shared_only():
    for extra in ({}, {'scope': 'history'}, {'verbose': True}, {'shared_only': False}, {'shared-only': False}):
        cmd = mcp.command('find', {'query': ['x'], **extra})
        assert '--shared-only' in cmd and cmd.index('--shared-only') < cmd.index('--')
