"""setup places Claude Code hooks and guardrails through the store's llmpro-harness plugins."""
import json
import os
import sys

import pytest

from llmpro import cli, harness, setup

FAKE = '''\
#!{py}
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
sf, lf = os.path.join(here, 'claude.json'), os.path.join(here, 'claude.log')
st = json.load(open(sf)) if os.path.exists(sf) else {{}}
st.setdefault('markets', {{}})
st.setdefault('plugins', {{}})
argv = sys.argv[1:]
open(lf, 'a').write(json.dumps(argv) + '\\n')
cmd = ' '.join(argv)
for bad in st.get('fail', []):
    if cmd.startswith(bad):
        sys.stderr.write('boom: ' + bad + '\\nsecond line\\n')
        sys.exit(3)
for bad in st.get('garbage', []):
    if cmd.startswith(bad):
        print('not json at all')
        sys.exit(0)
def save():
    json.dump(st, open(sf, 'w'))
if argv[:3] == ['plugin', 'marketplace', 'list']:
    print(json.dumps([{{'name': n, 'source': 'directory', 'path': p}} for n, p in st['markets'].items()]))
elif argv[:2] == ['plugin', 'list']:
    print(json.dumps([{{'id': i, 'version': '1.0', 'scope': 'user', 'enabled': e, 'installPath': '/x'}}
                      for i, e in st['plugins'].items()]))
elif argv[:3] == ['plugin', 'marketplace', 'add']:
    m = json.load(open(os.path.join(argv[3], '.claude-plugin', 'marketplace.json')))
    st['markets'][m['name']] = argv[3]
    save()
elif argv[:3] == ['plugin', 'marketplace', 'remove']:
    st['markets'].pop(argv[3], None)
    save()
elif argv[:2] == ['plugin', 'install']:
    assert argv[3:] == ['--scope', 'user'], argv
    if argv[2].split('@')[1] not in st['markets']:
        sys.stderr.write('no such marketplace\\n')
        sys.exit(1)
    st['plugins'][argv[2]] = True
    save()
elif argv[:2] == ['plugin', 'uninstall']:
    assert argv[3:] == ['--scope', 'user'], argv
    st['plugins'].pop(argv[2], None)
    save()
elif argv[:2] == ['mcp', 'get']:
    sys.exit(0 if argv[-1] in st.get('mcp', []) else 1)
elif argv[:2] == ['mcp', 'add']:
    st.setdefault('mcp', []).append('llmpro')
    save()
elif argv[:2] == ['mcp', 'remove']:
    st['mcp'] = [m for m in st.get('mcp', []) if m != argv[-1]]
    save()
else:
    sys.stderr.write('unknown: ' + cmd + '\\n')
    sys.exit(2)
'''

MARKET = {"name": "llmpro-harness", "owner": {"name": "llmpro"},
          "plugins": [{"name": "harness-hooks", "source": "./hooks/claude-code"},
                      {"name": "harness-guardrails", "source": "./guardrails/claude-code"}]}


class Fake:
    def __init__(self, folder):
        self.folder = folder
        self.json = folder / 'claude.json'
        self.log = folder / 'claude.log'
        script = folder / 'claude'
        script.write_text(FAKE.format(py=sys.executable))
        script.chmod(0o755)
        self.set(markets={}, plugins={})

    def state(self):
        return json.loads(self.json.read_text())

    def set(self, **kw):
        st = self.state() if self.json.exists() else {}
        st.update(kw)
        self.json.write_text(json.dumps(st))

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def changes(self):
        return [c for c in self.calls() if c[0] == 'plugin' and c[:2] != ['plugin', 'list'] and c[:3] != ['plugin', 'marketplace', 'list']]

    def listings(self):
        return [c for c in self.calls() if c[:2] == ['plugin', 'list'] or c[:3] == ['plugin', 'marketplace', 'list']]


@pytest.fixture(autouse=True)
def quiet_machine(monkeypatch, onpath):
    monkeypatch.setattr(setup, 'running_tools', lambda: [])


@pytest.fixture
def claude(tools_dir):
    return Fake(tools_dir)


def run(argv, capsys):
    code = cli.main(argv)
    c = capsys.readouterr()
    return code, c.out, c.err


def line(out, part):
    found = [l.strip() for l in out.splitlines() if l.strip().startswith(part + ':')]
    assert len(found) == 1, out
    return found[0]


def undo_text(out):
    lines = [l for l in out.splitlines() if l.startswith('To undo: sh ')]
    assert len(lines) == 1, out
    return open(lines[0][len('To undo: sh '):]).read()


def settle(home, capsys):
    """Run setup -y once on a machine with ~/.claude and the fake claude."""
    (home / '.claude').mkdir(exist_ok=True)
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 0, err
    return out, err


def test_no_claude_both_cells_not_here(home, store, capsys, tools_dir):
    (home / '.claude').mkdir()
    code, out, _ = run(['setup', '-n'], capsys)
    assert 'claude-code not here' in line(out, 'hooks')
    assert 'claude-code not here' in line(out, 'guardrails')
    assert harness.state(store, 'hooks', 'claude-code')[0] == 'not here'
    assert not (tools_dir / 'claude.log').exists()
    # claude present but no ~/.claude: also not here, and no call
    c = Fake(tools_dir)
    os.rmdir(home / '.claude')
    assert harness.state(store, 'guardrails', 'claude-code')[0] == 'not here'
    assert c.calls() == []


def test_missing_then_placed(home, store, capsys, claude):
    (home / '.claude').mkdir()
    code, out, _ = run(['setup', '-n'], capsys)
    assert code == 1
    assert 'claude-code missing' in line(out, 'hooks')
    assert 'claude-code missing' in line(out, 'guardrails')
    todo = [i for i in setup.survey(store, None) if i.name == 'harness'][0].todo
    assert todo.count(f'claude plugin marketplace add {store}') == 1
    assert 'claude plugin install harness-hooks@llmpro-harness --scope user' in todo
    assert 'claude plugin install harness-guardrails@llmpro-harness --scope user' in todo
    assert claude.changes() == []
    settle(home, capsys)
    mfile = os.path.join(store, '.claude-plugin/marketplace.json')
    assert open(mfile).read() == json.dumps(MARKET, indent=2) + '\n'
    for part, name in (('hooks', 'harness-hooks'), ('guardrails', 'harness-guardrails')):
        base = os.path.join(store, part, 'claude-code')
        pj = json.load(open(os.path.join(base, '.claude-plugin/plugin.json')))
        assert pj['name'] == name and 'version' not in pj and pj['description']
        assert open(os.path.join(base, 'hooks/hooks.json')).read() == json.dumps({'hooks': {}}, indent=2) + '\n'
    ch = claude.changes()
    assert [c for c in ch if c[:3] == ['plugin', 'marketplace', 'add']] == [['plugin', 'marketplace', 'add', store]]
    assert sorted(c[2] for c in ch if c[:2] == ['plugin', 'install']) == \
        ['harness-guardrails@llmpro-harness', 'harness-hooks@llmpro-harness']
    before = len(claude.listings())
    code, out, _ = run(['setup', '-n'], capsys)
    assert code == 0, out
    assert 'claude-code placed' in line(out, 'hooks')
    assert len(claude.changes()) == len(ch)
    assert len(claude.listings()) - before == 2  # one list of each kind per survey


def test_existing_marketplace_json_untouched_and_reported(home, store, capsys, claude):
    (home / '.claude').mkdir()
    p = os.path.join(store, '.claude-plugin/marketplace.json')
    os.makedirs(os.path.dirname(p))
    open(p, 'w').write('{"name": "llmpro-harness", "plugins": []}\n')
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 0, err
    assert open(p).read() == '{"name": "llmpro-harness", "plugins": []}\n'
    code, out, _ = run(['setup', '-n'], capsys)
    assert code == 0, out
    assert 'marketplace.json' in out and 'harness-hooks' in out
    assert 'missing' not in line(out, 'hooks')
    assert claude.changes() == []
    open(p, 'w').write('{nope')
    out = run(['setup', '-n'], capsys)[1]
    assert 'marketplace.json' in out and 'does not parse' in out


def test_marketplace_elsewhere(home, store, capsys, claude):
    (home / '.claude').mkdir()
    claude.set(markets={'llmpro-harness': '/somewhere/else'})
    run(['setup', '-y'], capsys)
    assert claude.changes() == []
    out = run(['setup', '-n'], capsys)[1]
    assert 'claude-code linked elsewhere' in line(out, 'hooks')
    assert '/somewhere/else' in out and 'claude plugin marketplace remove llmpro-harness' in out


def test_disabled_is_not_missing(home, store, capsys, claude):
    settle(home, capsys)
    claude.set(plugins={'harness-hooks@llmpro-harness': False, 'harness-guardrails@llmpro-harness': True})
    n = len(claude.changes())
    code, out, _ = run(['setup', '-n'], capsys)
    assert code == 0, out
    assert 'claude-code disabled' in line(out, 'hooks')
    assert 'claude plugin enable harness-hooks@llmpro-harness' in out
    assert len(claude.changes()) == n


def test_install_failure(home, store, capsys, claude):
    (home / '.claude').mkdir()
    claude.set(fail=['plugin install harness-guardrails'])
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 2
    assert 'plugin install harness-guardrails@llmpro-harness --scope user' in err
    assert '3' in err and 'boom' in err
    undo = undo_text(out)
    assert 'claude plugin marketplace remove llmpro-harness' in undo
    assert 'claude plugin uninstall harness-hooks@llmpro-harness --scope user' in undo


def test_garbage_is_unknown(home, store, capsys, claude):
    (home / '.claude').mkdir()
    claude.set(garbage=['plugin list'])
    code, out, _ = run(['setup', '-n'], capsys)
    assert 'claude-code unknown' in line(out, 'hooks')
    assert 'claude plugin list --json' in out
    st = harness.state(store, 'hooks', 'claude-code')
    assert st[0] == 'unknown' and 'claude plugin list --json' in st[1]
    claude.set(garbage=[], fail=['plugin marketplace list'])
    st = harness.state(store, 'hooks', 'claude-code')
    assert st[0] == 'unknown' and 'exit' in st[1] and 'boom' in st[1] and 'second line' not in st[1]
    run(['setup', '-y'], capsys)
    assert [c for c in claude.changes() if c[:2] == ['plugin', 'install']] == []


def test_uninstall(home, store, capsys, claude):
    settle(home, capsys)
    code, out, _ = run(['uninstall', '-n'], capsys)
    assert code == 1
    assert 'claude plugin uninstall harness-hooks@llmpro-harness' in out
    assert 'claude plugin marketplace remove llmpro-harness' in out
    assert 'hooks/claude-code/hooks/hooks.json' in out
    n = len(claude.changes())
    code, out, err = run(['uninstall', '-y'], capsys)
    assert code == 0, err
    ch = claude.changes()[n:]
    assert sorted(c[2] for c in ch if c[:2] == ['plugin', 'uninstall']) == \
        ['harness-guardrails@llmpro-harness', 'harness-hooks@llmpro-harness']
    assert ['plugin', 'marketplace', 'remove', 'llmpro-harness'] in ch
    assert os.path.isfile(os.path.join(store, '.claude-plugin/marketplace.json'))
    assert os.path.isfile(os.path.join(store, 'hooks/claude-code/hooks/hooks.json'))
    undo = undo_text(out)
    assert 'claude plugin marketplace add' in undo
    assert 'claude plugin install harness-hooks@llmpro-harness --scope user' in undo
    assert claude.state()['plugins'] == {} and claude.state()['markets'] == {}
