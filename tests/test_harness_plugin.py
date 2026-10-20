"""setup places Claude Code hooks and guardrails through the store's llmpro-harness plugins."""
import json
import os
import shutil
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
    key = st.get('where_key', 'path')
    print(json.dumps([{{'name': n, 'source': 'directory', key: p}} for n, p in st['markets'].items()]))
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
    # ~/.claude is there but the command is not: say so once, with what to do
    why = ('~/.claude exists, but the claude command is not on PATH, so llmpro cannot install the plugins '
           'harness-hooks and harness-guardrails. Install Claude Code or add its folder to PATH, then run llmpro setup')
    assert out.count(why) == 1, out
    err = run(['setup', '-y'], capsys)[2]
    assert why in err[err.index('For you to do by hand:'):]
    # claude present but no ~/.claude: also not here, and no call
    c = Fake(tools_dir)
    shutil.rmtree(home / '.claude')
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
    want = ('~/.agents/.claude-plugin/marketplace.json does not list harness-hooks and harness-guardrails; '
            'llmpro does not overwrite it, so those plugins are not installed. Add '
            '{"name": "harness-hooks", "source": "./hooks/claude-code"}, '
            '{"name": "harness-guardrails", "source": "./guardrails/claude-code"} to its "plugins" list, '
            'or rm ~/.agents/.claude-plugin/marketplace.json to let setup write a fresh one, then run llmpro setup')
    assert out.count(want) == 1, out
    assert 'missing' not in line(out, 'hooks')
    assert claude.changes() == []
    open(p, 'w').write('{nope')
    out = run(['setup', '-n'], capsys)[1]
    assert out.count('~/.agents/.claude-plugin/marketplace.json is not valid JSON (') == 1, out
    assert ('llmpro does not overwrite it, so the plugins harness-hooks and harness-guardrails are not installed. '
            'Fix the JSON, or rm ~/.agents/.claude-plugin/marketplace.json to let setup write a fresh one, '
            'then run llmpro setup') in out
    err = run(['setup', '-y'], capsys)[2]
    assert 'is not valid JSON' in err[err.index('For you to do by hand:'):]


def test_marketplace_elsewhere(home, store, capsys, claude):
    (home / '.claude').mkdir()
    claude.set(markets={'llmpro-harness': '/somewhere/else'})
    run(['setup', '-y'], capsys)
    assert claude.changes() == []
    out = run(['setup', '-n'], capsys)[1]
    assert 'claude-code linked elsewhere' in line(out, 'hooks')
    assert ('the Claude Code marketplace llmpro-harness points at /somewhere/else, not at this store; llmpro left it. '
            'To use this store: claude plugin marketplace remove llmpro-harness (this also uninstalls the plugins '
            'installed from it), then run llmpro setup') in out
    assert out.count('points at /somewhere/else') == 1, out


def test_marketplace_folder_given_as_install_location(home, store, capsys, claude):
    settle(home, capsys)
    claude.set(where_key='installLocation')
    code, out, _ = run(['setup', '-n'], capsys)
    assert code == 0, out
    assert 'claude-code placed' in line(out, 'hooks')


def test_disabled_is_not_missing(home, store, capsys, claude):
    settle(home, capsys)
    claude.set(plugins={'harness-hooks@llmpro-harness': False, 'harness-guardrails@llmpro-harness': True})
    n = len(claude.changes())
    code, out, _ = run(['setup', '-n'], capsys)
    assert code == 0, out
    assert 'claude-code disabled' in line(out, 'hooks')
    assert ('harness-hooks@llmpro-harness is installed but turned off; llmpro leaves it off. '
            'To turn it on: claude plugin enable harness-hooks@llmpro-harness') in out
    err = run(['setup', '-y'], capsys)[2]
    assert 'For you to do by hand:' not in err  # turning it off was the person's choice
    assert len(claude.changes()) == n


def test_install_failure(home, store, capsys, claude):
    (home / '.claude').mkdir()
    claude.set(fail=['plugin install harness-guardrails'])
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 2
    assert 'plugin install harness-guardrails@llmpro-harness --scope user' in err
    assert 'failed with exit status 3: boom: plugin install harness-guardrails' in err
    path = [l for l in out.splitlines() if l.startswith('To undo: sh ')][0][len('To undo: sh '):]
    assert ('It stopped there. The lines above show what it changed before that; the steps after it were not done.'
            in err)
    assert ('Next: fix the cause (run the command yourself to see its whole message), then run llmpro setup again; '
            f'it does only what is still missing. To take back what this run changed instead: sh {path}') in err
    assert 'Already done' not in err
    undo = undo_text(out)
    assert 'claude plugin marketplace remove llmpro-harness' in undo
    assert 'claude plugin uninstall harness-hooks@llmpro-harness --scope user' in undo


def test_garbage_is_unknown(home, store, capsys, claude):
    (home / '.claude').mkdir()
    claude.set(garbage=['plugin list'])
    code, out, _ = run(['setup', '-n'], capsys)
    assert 'claude-code unknown' in line(out, 'hooks')
    assert out.count('`claude plugin list --json` exited with status 0 but printed no JSON: not json at all.') == 1, out
    assert ('llmpro cannot tell whether the plugins harness-hooks and harness-guardrails are installed, so setup '
            'leaves them. Run that command yourself to see why, fix it, then run llmpro setup') in out
    st = harness.state(store, 'hooks', 'claude-code')
    assert st[0] == 'unknown' and 'claude plugin list --json' in st[1]
    claude.set(garbage=[], fail=['plugin marketplace list'])
    st = harness.state(store, 'hooks', 'claude-code')
    assert st[0] == 'unknown' and 'exit' in st[1] and 'boom' in st[1] and 'second line' not in st[1]
    err = run(['setup', '-y'], capsys)[2]
    assert [c for c in claude.changes() if c[:2] == ['plugin', 'install']] == []
    assert 'llmpro cannot tell whether' in err[err.index('For you to do by hand:'):]


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


def test_preview_lists_steps_in_the_order_apply_does_them(home, store, capsys, claude):
    (home / '.claude').mkdir()
    todo = [i for i in setup.survey(store, None) if i.name == 'harness'][0].todo
    kinds = [setup.harness_step(t) for t in todo]
    assert kinds == sorted(kinds) and set(kinds) == {0, 1, 2, 3}, todo
    assert todo.index('create hooks/ with a README.md') < todo.index('create hooks/claude-code/hooks/hooks.json')


def test_uninstall_failure_says_what_next(home, store, capsys, claude):
    settle(home, capsys)
    claude.set(fail=['plugin uninstall harness-guardrails'])
    code, out, err = run(['uninstall', '-y'], capsys)
    assert code == 2
    path = [l for l in out.splitlines() if l.startswith('To undo: sh ')][0][len('To undo: sh '):]
    assert 'failed with exit status 3: boom: plugin uninstall harness-guardrails' in err
    assert 'It stopped there.' in err and 'then run llmpro uninstall again; it does only what is still missing.' in err
    assert f'To take back what this run changed instead: sh {path}' in err
