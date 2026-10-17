"""find, check --near-miss, check --fix and the health checks, and the MCP server, on a temporary store."""
import json
import os
import subprocess
import sys

import pytest

from llmpro import cli


def put(store, rel, text):
    p = os.path.join(store, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(text)


def conv(title, summary, body):
    return (f'---\nid: codex:abc\ntool: codex\nmachine: m1\ntitle: {title}\n'
            f'summary: |\n  {summary}\n  second line\n---\n### user\n{body}\n')


def memory(name, scope, mtype='project', updated='2026-09-30', desc='a fact'):
    return (f'---\nname: {name}\ndescription: {desc}\ntype: {mtype}\nscope: {scope}\nstatus: active\n'
            f'created: 2026-09-01\nupdated: {updated}\n---\nThe body about pelicans.\n')


@pytest.fixture
def filled(store):
    put(store, 'history/m1/codex/2026/2026-09-10_alpha_aaaa1111.md',
        conv('Alpha chat', 'Set up the ansible role', 'please run Ansible now\nsecond ANSIBLE line\nthird ansible\nfourth ansible\n'))
    put(store, 'history/m2/claude-code/2026/2026-10-05_beta_bbbb2222.md',
        conv('Beta chat', 'Fixed docker', 'ansible and docker together ' + 'x' * 300 + '\n'))
    put(store, 'history/m2/claude-code/2026/2026-10-06_gamma_cccc3333.md', conv('Gamma', 'only docker', 'docker\n'))
    put(store, 'local/history/m1/codex/2026/2026-10-07_loc_dddd4444.md', conv('Local one', 'private ansible', 'ansible\n'))
    put(store, 'memory/pelican.md', memory('pelican', 'zoo'))
    put(store, 'memory/old.md', memory('old', 'm1', updated='2026-01-01'))
    put(store, 'sessions/codex/raw.jsonl', '{"x": "ansible raw"}\n')
    return store


def find(store, capsys, *args):
    code = cli.main(['find', '--store', store, *args])
    return code, capsys.readouterr()


def test_find_words_are_anded_and_case_insensitive(filled, capsys):
    code, cap = find(filled, capsys, 'ANSIBLE', 'docker')
    assert code == 0
    assert len(cap.out.splitlines()) == 1 and 'Beta chat' in cap.out


def test_find_line_format_and_order(filled, capsys):
    code, cap = find(filled, capsys, 'ansible')
    lines = cap.out.splitlines()
    assert code == 0 and len(lines) == 3  # two history, one local; sessions/ never read
    assert lines[0].startswith('2026-10-07') and 'local/history/m1/codex/2026/2026-10-07_loc_dddd4444.md' in lines[0]
    assert 'm2/claude-code' in lines[1] and lines[1].endswith('- Fixed docker')
    assert 'raw' not in cap.out


def test_find_nothing_exits_1(filled, capsys):
    code, cap = find(filled, capsys, 'zebra')
    assert code == 1 and cap.out == ''


def test_find_filters(filled, capsys):
    assert len(find(filled, capsys, 'ansible', '--machine', 'm1')[1].out.splitlines()) == 2
    assert len(find(filled, capsys, 'ansible', '--tool', 'claude-code')[1].out.splitlines()) == 1
    assert len(find(filled, capsys, 'ansible', '--since', '2026-10-01')[1].out.splitlines()) == 2
    assert find(filled, capsys, 'ansible', '--memory')[0] == 1
    assert len(find(filled, capsys, 'pelicans', '--history')[1].out.splitlines()) == 0


def test_find_memory(filled, capsys):
    code, cap = find(filled, capsys, 'pelicans')
    lines = cap.out.splitlines()
    assert code == 0 and len(lines) == 2
    assert lines[0].startswith('2026-09-30') and 'memory/zoo' in lines[0] and 'memory/pelican.md' in lines[0]
    assert lines[0].endswith('- a fact')
    assert len(find(filled, capsys, 'pelicans', '--machine', 'm1')[1].out.splitlines()) == 1  # scope m1
    assert find(filled, capsys, 'pelicans', '--tool', 'codex')[0] == 1  # memories have no tool


def test_find_verbose_lines(filled, capsys):
    code, cap = find(filled, capsys, 'ansible', '-v', '--machine', 'm1', '--history')
    out = cap.out.splitlines()
    assert code == 0
    assert sum(1 for line in out if line.startswith('    ')) <= 6
    alpha = out[out.index(next(x for x in out if 'Alpha' in x)) + 1:][:3]
    assert len(alpha) == 3 and all(line.startswith('    ') for line in alpha)
    code, cap = find(filled, capsys, 'docker', 'together', '-v')
    assert all(len(line.strip()) <= 160 for line in cap.out.splitlines() if line.startswith('    '))
    assert any(line.startswith('    ') and len(line.strip()) == 160 for line in cap.out.splitlines())


def test_find_json(filled, capsys):
    code, cap = find(filled, capsys, 'pelicans', '--json')
    got = json.loads(cap.out)
    assert code == 0 and got['ok'] is True and got['command'] == 'find'
    assert got['results'][0]['kind'] == 'memory' and got['results'][0]['path'] == 'memory/pelican.md'
    assert json.loads(find(filled, capsys, 'zebra', '--json')[1].out)['results'] == []


# ---------------------------------------------------------------- health, --fix

def test_health_ok_on_consistent_store(store, capsys):
    put(store, 'memory/a.md', memory('a', 'global'))
    assert cli.main(['check', '--fix', '--store', store]) == 0
    capsys.readouterr()
    assert cli.main(['check', '--store', store]) == 0


def test_stale_index_fails_and_fix_repairs(store, capsys):
    put(store, 'memory/a.md', memory('a', 'global'))
    put(store, 'memory/INDEX.md', '# stale\n')
    assert cli.main(['check', '--store', store]) == 1
    assert 'INDEX.md is out of date' in capsys.readouterr().out
    assert cli.main(['check', '--fix', '--store', store]) == 0
    assert '[A](a.md)' in open(os.path.join(store, 'memory', 'INDEX.md')).read()


def test_memory_without_frontmatter_is_a_problem_and_not_fixed(store, capsys):
    put(store, 'memory/bad.md', '---\nname: bad\n---\nbody\n')
    cli.main(['check', '--fix', '--store', store])
    capsys.readouterr()
    before = open(os.path.join(store, 'memory', 'bad.md')).read()
    assert cli.main(['check', '--fix', '--store', store]) == 1
    out = capsys.readouterr().out
    assert 'memory/bad.md' in out and 'lack required frontmatter' in out
    assert open(os.path.join(store, 'memory', 'bad.md')).read() == before


def test_history_missing_from_manifest(store, capsys):
    rel = 'history/m1/codex/2026/2026-09-10_alpha_aaaa1111.md'
    put(store, rel, conv('A', 's', 'b'))
    assert cli.main(['check', '--store', store]) == 1
    assert 'not in manifest.jsonl' in capsys.readouterr().out
    row = {'tool': 'codex', 'machine': 'm1', 'kind': 'session', 'source_path': '/home/u/x', 'original_id': 'abc',
           'mtime': '', 'size': 1, 'output': rel, 'status': 'imported'}
    put(store, 'manifest.jsonl', json.dumps(row) + '\n')
    assert cli.main(['check', '--store', store]) == 0


def test_staged_check_skips_health(store, capsys):
    put(store, 'memory/bad.md', '---\nname: bad\n---\n')
    assert cli.main(['check', '--staged', '--store', store]) == 0


# ---------------------------------------------------------------- near-miss

def test_near_miss_reports_masked_and_exits_0(store, capsys):
    secret = 'Zq8vL2mWp' + 'x7Rt4Kd9Hs3Bn'
    put(store, 'history/m1/codex/2026/2026-09-10_a_aaaa1111.md', f'some text {secret} more\n')
    assert cli.main(['check', '--near-miss', '--store', store]) == 0
    out = capsys.readouterr().out
    assert 'random' in out and 'Zq8' in out and secret not in out
    assert '1 files read, 1 groups' in out


def test_near_miss_json_and_exclusive(store, capsys):
    assert cli.main(['check', '--near-miss', '--json', '--store', store]) == 0
    assert json.loads(capsys.readouterr().out)['exit'] == 0
    assert cli.main(['check', '--near-miss', '--fix', '--store', store]) == 2


# ---------------------------------------------------------------- mcp

class Server:
    def __init__(self, store):
        self.p = subprocess.Popen([sys.executable, '-m', 'llmpro', '--store', store, 'mcp'],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.n = 0

    def ask(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': self.n, 'method': method, 'params': params or {}}) + '\n')
        self.p.stdin.flush()
        resp = json.loads(self.p.stdout.readline())
        assert resp['id'] == self.n
        return resp

    def close(self):
        self.p.stdin.close()
        assert self.p.wait(timeout=10) == 0


@pytest.fixture
def server(filled):
    s = Server(filled)
    yield s
    s.close()


def test_mcp_handshake(server):
    r = server.ask('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}})
    assert r['result']['protocolVersion'] == '2025-06-18' and 'tools' in r['result']['capabilities']
    server.p.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
    assert server.ask('ping')['result'] == {}
    tools = server.ask('tools/list')['result']['tools']
    assert [t['name'] for t in tools] == ['find', 'check', 'sync_status']
    assert all(t['annotations']['readOnlyHint'] is True for t in tools)


def test_mcp_find(server):
    r = server.ask('tools/call', {'name': 'find', 'arguments': {'query': ['ansible', 'docker']}})['result']
    assert r['isError'] is False
    hits = json.loads(r['content'][0]['text'])['results']
    assert len(hits) == 1 and hits[0]['title'] == 'Beta chat'
    r = server.ask('tools/call', {'name': 'find', 'arguments': {'query': ['zebra']}})['result']
    assert json.loads(r['content'][0]['text'])['results'] == []
    r = server.ask('tools/call', {'name': 'find', 'arguments': {'query': []}})['result']
    assert r['isError'] is True


def test_mcp_check_is_read_only(server, filled):
    put(filled, 'memory/INDEX.md', '# stale\n')
    r = server.ask('tools/call', {'name': 'check', 'arguments': {}})['result']
    got = json.loads(r['content'][0]['text'])
    assert r['isError'] is False and got['passed'] is False
    assert open(os.path.join(filled, 'memory', 'INDEX.md')).read() == '# stale\n'


def test_mcp_sync_status(server):
    r = server.ask('tools/call', {'name': 'sync_status', 'arguments': {}})['result']
    got = json.loads(r['content'][0]['text'])
    assert r['isError'] is False and 'sessions' in got


def test_mcp_errors(server):
    assert server.ask('nope')['error']['code'] == -32601
    assert server.ask('tools/call', {'name': 'nope'})['error']['code'] == -32602
