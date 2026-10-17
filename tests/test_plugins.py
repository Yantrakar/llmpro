"""Package 03: outside programs, the gitpro adapter, the plugin contract and registry, `llmpro git`, setup wiring."""
import importlib.metadata
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from llmpro import cli, gitpro, hot, plugins, setup, tools
from llmpro.plugins import base


def run_json(argv, capsys):
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------- tools.which

def test_which_looks_only_in_the_tool_path(tools_dir):
    fake = tools_dir / 'sometool'
    fake.write_text('#!/bin/sh\n')
    fake.chmod(0o755)
    assert tools.which('sometool') == str(fake)
    assert tools.which('git') is None  # git is on PATH, but not in the tool path


def test_which_with_an_empty_tool_path_finds_nothing(monkeypatch):
    monkeypatch.setenv('LLMPRO_TOOL_PATH', '')
    assert tools.which('git') is None


def test_which_without_the_variable_uses_path(monkeypatch):
    monkeypatch.delenv('LLMPRO_TOOL_PATH')
    assert tools.which('git')


def test_gitpro_is_found_through_which(tools_dir, monkeypatch):
    monkeypatch.delenv('LLMPRO_GITPRO')
    fake = tools_dir / 'gitpro'
    fake.write_text('#!/bin/sh\n')
    fake.chmod(0o755)
    gitpro.reset_cache()
    assert gitpro.get().path == str(fake)


# ---------------------------------------------------------------- the adapter

def test_missing(tmp_path):
    g = gitpro.get()
    assert g.path is None
    st = g.status()
    assert st.state == 'missing' and 'pipx install gitpro' in st.hint
    assert not g.has('why')


def test_env_path_never_falls_back_to_path(tools_dir, monkeypatch):
    fake = tools_dir / 'gitpro'
    fake.write_text('#!/bin/sh\n')
    fake.chmod(0o755)
    monkeypatch.setenv('LLMPRO_GITPRO', '')
    gitpro.reset_cache()
    assert gitpro.get().path is None


def test_too_old(fake_gitpro):
    fake_gitpro.version('0.3.4')
    st = gitpro.get().status()
    assert st.state == 'too_old' and '0.3.4' in st.reason and st.hint == 'pipx upgrade gitpro'
    assert not gitpro.get().has('why')


def test_ok(fake_gitpro):
    g = gitpro.get()
    assert g.status().state == 'ok'
    assert g.version == '0.4.0' and 'why' in g.capabilities
    assert g.has('why') and not g.has('nonsense')
    assert fake_gitpro.calls() == [['--version', '--json']]  # asked once, even after has()


def test_version_suffix_is_ignored(fake_gitpro):
    fake_gitpro.version('0.4.1rc1')
    assert gitpro.get().status().state == 'ok'
    assert gitpro.parse_version('0.10.2') > (0, 9, 9)
    assert gitpro.parse_version('1.2') == (1, 2, 0)


def test_version_answer_is_not_json(fake_gitpro):
    fake_gitpro.version_raw('gitpro 0.3.1')
    st = gitpro.get().status()
    assert st.state == 'too_old' and 'too old' in st.reason


def test_version_timeout(fake_gitpro):
    fake_gitpro.slow_version(5)
    g = gitpro.get()
    g.version_timeout = 0.3
    assert g.status().state == 'too_old' and 'timed out' in g.status().reason


def test_run_json_returns_a_failing_exit_with_its_json(fake_gitpro):
    body = {'ok': True, 'command': 'why', 'problems': ['x']}
    fake_gitpro.answer(['why', '.', '--json'], body, exit=1)
    assert gitpro.get().run_json(['why', '.', '--json']) == (1, body)


def test_run_json_with_text_raises(fake_gitpro):
    fake_gitpro.answer(['why'], 'not json at all')
    with pytest.raises(gitpro.GitproError):
        gitpro.get().run_json(['why'])
    fake_gitpro.answer(['list'], [1, 2])
    with pytest.raises(gitpro.GitproError):
        gitpro.get().run_json(['list'])


def test_run_json_times_out(fake_gitpro):
    fake_gitpro.answer(['slow'], {'ok': True}, sleep=5)
    with pytest.raises(gitpro.GitproError, match='timed out'):
        gitpro.get().run_json(['slow'], timeout=0.3)


def test_run_json_without_gitpro_raises():
    with pytest.raises(gitpro.GitproError):
        gitpro.get().run_json(['why'])


def test_the_child_environment(fake_gitpro, tmp_path, monkeypatch):
    prog = Path(fake_gitpro.path)
    prog.write_text(f'#!{sys.executable}\nimport os, json\n'
                    'print(json.dumps({"c": os.environ.get("NO_COLOR"), "d": os.environ.get("GITPRO_NO_DEPRECATION")}))\n')
    assert gitpro.get().run_json(['x']) == (0, {'c': '1', 'd': '1'})


# ---------------------------------------------------------------- the fake itself

def test_fake_unanswered_argv_fails_loudly(fake_gitpro, capfd):
    code = gitpro.get().run(['nothing', 'here'])
    assert code == 2
    assert "fake gitpro: no answer for ['nothing', 'here']" in capfd.readouterr().err


def test_fake_mcp_server(fake_gitpro):
    import subprocess
    tool = {'name': 'why', 'description': 'x', 'inputSchema': {'type': 'object'}}
    result = {'content': [{'type': 'text', 'text': 'hi'}]}
    fake_gitpro.mcp([tool], {'why': result})
    msgs = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26'}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'why'}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'other'}}]
    r = subprocess.run([fake_gitpro.path, 'mcp'], input=''.join(json.dumps(m) + '\n' for m in msgs),
                       capture_output=True, text=True, timeout=20)
    replies = [json.loads(line) for line in r.stdout.splitlines()]
    assert [x['id'] for x in replies] == [1, 2, 3, 4]
    assert replies[0]['result']['protocolVersion'] == '2025-03-26'
    assert replies[1]['result']['tools'] == [tool] and replies[2]['result'] == result
    assert replies[3]['result']['isError'] is True
    assert os.access(fake_gitpro.path, os.X_OK) and stat.S_IMODE(os.stat(fake_gitpro.path).st_mode) & 0o100
    with open(fake_gitpro.path) as fh:
        assert fh.readline().strip() == f'#!{sys.executable}'


# ---------------------------------------------------------------- the registry

def test_builtins_come_from_entry_points():
    eps = importlib.metadata.entry_points(group=plugins.GROUP)
    assert sorted(ep.name for ep in eps) == ['git', 'memory', 'secrets']
    assert [p.name for p in plugins.load()] == ['git', 'memory', 'secrets']
    assert all(p.api == base.API for p in plugins.load())
    assert plugins.get('git') is plugins.load()[0] and plugins.get('nope') is None


class FakeEntryPoint:
    def __init__(self, name, obj=None, error=None):
        self.name, self._obj, self._error = name, obj, error

    def load(self):
        if self._error:
            raise self._error
        return self._obj


def patch_entry_points(monkeypatch, eps):
    monkeypatch.setattr(importlib.metadata, 'entry_points', lambda **kw: list(eps) if kw.get('group') == plugins.GROUP else [])
    plugins.reset_cache()


class Named(base.Plugin):
    def __init__(self, name, api=base.API):
        self.name, self.api = name, api


def test_a_broken_entry_point_is_skipped_with_a_warning(monkeypatch, capsys):
    patch_entry_points(monkeypatch, [FakeEntryPoint('bad', error=ImportError('no module')),
                                     FakeEntryPoint('good', Named('good'))])
    assert [p.name for p in plugins.load()] == ['good']
    err = capsys.readouterr().err
    assert err.count('llmpro: plugin bad skipped:') == 1 and 'no module' in err
    plugins.load()
    assert capsys.readouterr().err == ''  # one warning per process


def test_wrong_api_not_a_plugin_and_repeated_names_are_skipped(monkeypatch, capsys):
    patch_entry_points(monkeypatch, [FakeEntryPoint('old', Named('old', api='llmpro-plugin.v0')),
                                     FakeEntryPoint('plain', object()),
                                     FakeEntryPoint('one', Named('same')),
                                     FakeEntryPoint('two', Named('same')),
                                     FakeEntryPoint('anon', Named(''))])
    assert [p.name for p in plugins.load()] == ['same']
    err = capsys.readouterr().err
    for word in ('plugin old skipped', 'plugin plain skipped', 'plugin two skipped', 'plugin anon skipped'):
        assert word in err
    assert 'plugin one skipped' not in err


def test_fallback_without_metadata(monkeypatch):
    patch_entry_points(monkeypatch, [])
    assert [p.name for p in plugins.load()] == ['git', 'memory', 'secrets']


def test_builtin_capabilities(fake_gitpro):
    ctx = plugins.context()
    assert plugins.get('memory').capabilities(ctx) == ['memory.sync', 'memory.find', 'memory.summary']
    assert plugins.get('secrets').capabilities(ctx) == ['secrets.check', 'secrets.store-hooks']
    git = plugins.get('git')
    assert 'git' in git.capabilities(ctx) and 'git.why' in git.capabilities(ctx)
    assert git.mcp_child(ctx) == [fake_gitpro.path, 'mcp']
    # what each plugin's setup items are is tested with the package that adds them (05, 06, 08)
    assert all(isinstance(p.setup_items(ctx), list) for p in plugins.load())


def test_git_plugin_when_gitpro_is_missing_or_old(fake_gitpro):
    ctx = plugins.context()
    fake_gitpro.version('0.3.0')
    git = plugins.get('git')
    assert git.status(ctx).state == 'too_old' and git.capabilities(ctx) == [] and git.mcp_child(ctx) is None


# ---------------------------------------------------------------- --version --json

def test_version_json_with_gitpro(fake_gitpro, capsys):
    code, got = run_json(['--version', '--json'], capsys)
    assert code == 0 and got['ok'] is True
    states = {p['name']: p for p in got['plugins']}
    assert set(states) == {'git', 'memory', 'secrets'}
    assert all(p['api'] == base.API for p in got['plugins'])
    assert states['git']['state'] == 'ok' and states['memory']['state'] == 'ok'
    caps = got['capabilities']
    assert caps == sorted(set(caps))
    for c in ('git', 'git.why', 'git.guard', 'memory.sync', 'secrets.check', 'check', 'version.json'):
        assert c in caps


def test_version_json_without_gitpro(capsys):
    code, got = run_json(['--version', '--json'], capsys)
    assert code == 0
    git = next(p for p in got['plugins'] if p['name'] == 'git')
    assert git['state'] == 'missing' and 'pipx install gitpro' in git['hint']
    assert not [c for c in got['capabilities'] if c == 'git' or c.startswith('git.')]
    assert 'memory.find' in got['capabilities']


# ---------------------------------------------------------------- llmpro git

def test_git_passes_arguments_through(fake_gitpro, capfd):
    fake_gitpro.answer(['why', '.', '--json'], {'ok': True, 'problems': ['x']}, exit=1)
    assert cli.main(['git', 'why', '.', '--json']) == 1
    assert '"problems"' in capfd.readouterr().out
    assert fake_gitpro.calls()[-1] == ['why', '.', '--json']


def test_git_passes_help_and_dashes_through(fake_gitpro, capfd):
    fake_gitpro.answer(['--help'], 'gitpro help text')
    assert cli.main(['git', '--help']) == 0
    assert 'gitpro help text' in capfd.readouterr().out
    fake_gitpro.answer(['-C', 'x', 'guard', 'status'], 'ok')
    assert cli.main(['git', '-C', 'x', 'guard', 'status']) == 0


def test_git_with_the_global_store_option(fake_gitpro, capfd, tmp_path):
    fake_gitpro.answer(['why'], 'fine')
    assert cli.main(['--store', str(tmp_path / 's'), 'git', 'why']) == 0


def test_git_missing_gives_2_and_the_hint(capsys):
    assert cli.main(['git', 'why', '.']) == 2
    err = capsys.readouterr().err
    assert 'not installed' in err and 'pipx install gitpro' in err


def test_git_too_old_gives_2_and_the_hint(fake_gitpro, capsys):
    fake_gitpro.version('0.3.4')
    assert cli.main(['git', 'why']) == 2
    assert 'pipx upgrade gitpro' in capsys.readouterr().err
    assert fake_gitpro.calls() == [['--version', '--json']]  # the command itself did not run


# ---------------------------------------------------------------- setup wiring

class Marker(base.SetupItem):
    name = 'marker'

    def __init__(self, path):
        self.path = Path(path)

    def survey(self, ctx):
        done = self.path.exists()
        return setup.Item('marker', not done, 'present' if done else 'missing', ['a flag file'],
                          [] if done else [f'create {self.path.name}'])

    def apply(self, ctx, undo):
        self.path.write_text('x')
        undo.run('rm', '-f', str(self.path))

    def plan_remove(self, ctx):
        return [f'remove {self.path.name}'] if self.path.exists() else []

    def remove(self, ctx, undo):
        self.path.unlink()
        undo.run('touch', str(self.path))
        return ['removed the flag file']


class Probe(base.Plugin):
    def __init__(self, name, items=(), status=None):
        self.name, self._items, self._status = name, list(items), status

    def status(self, ctx):
        return self._status or base.Status('ok')

    def setup_items(self, ctx):
        return self._items


def use_plugins(monkeypatch, *ps):
    monkeypatch.setattr(plugins, 'load', lambda: sorted(ps, key=lambda p: p.name))


def state_scripts(home):
    return sorted((home / 'state' / 'llmpro').glob('setup-undo-*.sh'))


def test_plugin_item_shows_in_dry_run(machine, tmp_path, monkeypatch, capsys):
    flag = tmp_path / 'flag'
    use_plugins(monkeypatch, Probe('tester', [Marker(flag)]))
    store = str(machine / '.agents')
    assert cli.main(['--store', store, 'setup', '-n']) == 1
    out = capsys.readouterr().out
    assert 'marker' in out and out.index('instruction pointers') < out.index('marker')
    code, got = run_json(['--store', store, 'setup', '-n', '--json'], capsys)
    assert code == 1 and got['ok'] is False and 'marker' in got['missing']
    item = next(i for i in got['items'] if i['name'] == 'marker')
    assert item['plugin'] == 'tester' and item['present'] is False
    assert all('plugin' not in i for i in got['items'] if i['name'] != 'marker')
    assert got['plugins'][0]['name'] == 'tester'
    assert not flag.exists()


def test_plugin_item_is_applied_after_the_core_items_with_one_undo(machine, tmp_path, monkeypatch, capsys, onpath):
    flag = tmp_path / 'flag'
    use_plugins(monkeypatch, Probe('tester', [Marker(flag)]))
    store = str(machine / '.agents')
    assert cli.main(['--store', store, 'setup', '-y']) == 0
    assert flag.exists() and os.path.isdir(store)
    scripts = state_scripts(machine)
    assert len(scripts) == 1
    body = scripts[0].read_text()
    assert f'rm -f {flag}' in body
    lines = [l for l in body.splitlines() if not l.startswith('#')]
    assert lines[0].startswith('rm -f') and str(flag) in lines[0]  # undone first: it was done last
    assert cli.main(['--store', store, 'setup', '-n']) == 0


def test_only_store_skips_the_plugin(machine, tmp_path, monkeypatch, onpath):
    flag = tmp_path / 'flag'
    use_plugins(monkeypatch, Probe('tester', [Marker(flag)]))
    store = str(machine / '.agents')
    assert cli.main(['--store', store, 'setup', '-y', '--only', 'store']) == 0
    assert not flag.exists() and os.path.isdir(store)


def test_only_a_plugin_skips_the_core(machine, tmp_path, monkeypatch, capsys):
    flag = tmp_path / 'flag'
    use_plugins(monkeypatch, Probe('tester', [Marker(flag)]), Probe('other', [Marker(tmp_path / 'other')]))
    store = str(machine / '.agents')
    code, got = run_json(['--store', store, 'setup', '-n', '--json', '--only', 'tester'], capsys)
    assert [i['name'] for i in got['items']] == ['marker'] and got['items'][0]['plugin'] == 'tester'


def test_only_with_an_unknown_name_is_an_error(machine, monkeypatch, capsys):
    use_plugins(monkeypatch, Probe('tester'))
    assert cli.main(['--store', str(machine / '.agents'), 'setup', '-n', '--only', 'nope']) == 2
    assert 'nope' in capsys.readouterr().err


def test_a_plugin_that_is_not_ok_informs_and_does_not_fail_dry_run(machine, tmp_path, monkeypatch, capsys, onpath):
    store = str(machine / '.agents')
    assert cli.main(['--store', store, 'setup', '-y']) == 0
    capsys.readouterr()
    st = base.Status('missing', 'gitpro is not installed', 'pipx install gitpro')
    use_plugins(monkeypatch, Probe('tester', [Marker(tmp_path / 'flag')], status=st))
    code, got = run_json(['--store', store, 'setup', '-n', '--json'], capsys)
    assert code == 0 and got['ok'] is True and got['missing'] == []
    item = next(i for i in got['items'] if i['name'] == 'plugin tester')
    assert item['present'] is True and item['plugin'] == 'tester' and 'pipx install gitpro' in item['details']
    assert not any(i['name'] == 'marker' for i in got['items'])  # its items are not asked about
    assert cli.main(['--store', store, 'setup', '-n']) == 0
    out = capsys.readouterr().out
    assert 'plugin tester' in out and 'gitpro is not installed' in out


def test_uninstall_shows_and_runs_plugin_removal_first(machine, tmp_path, monkeypatch, capsys, onpath):
    flag = tmp_path / 'flag'
    store = str(machine / '.agents')
    assert cli.main(['--store', store, 'setup', '-y']) == 0
    use_plugins(monkeypatch, Probe('tester', [Marker(flag)]))
    assert cli.main(['--store', store, 'setup', '-y']) == 0 and flag.exists()
    capsys.readouterr()
    code, got = run_json(['--store', store, 'uninstall', '-n', '--json'], capsys)
    assert code == 1 and '[tester] remove flag' in got['todo']
    assert flag.exists()
    assert cli.main(['--store', store, 'uninstall', '-y']) == 0
    assert not flag.exists()
    assert cli.main(['--store', store, 'uninstall', '-n']) == 0


# ---------------------------------------------------------------- the hot path

def test_hook_does_not_load_the_registry(monkeypatch):
    for name in [n for n in sys.modules if n == 'llmpro.plugins' or n.startswith('llmpro.plugins.') or n == 'llmpro.gitpro']:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.delitem(sys.modules, 'importlib.metadata', raising=False)
    seen = []
    monkeypatch.setattr(hot, 'main', lambda argv: seen.append(argv) or 0)
    assert cli.main(['hook', 'pre-commit']) == 0 and seen == [['hook', 'pre-commit']]
    assert cli.main(['merge-driver', 'manifest', 'a', 'b', 'c']) == 0
    assert not [n for n in sys.modules if n.startswith('llmpro.plugins') or n == 'llmpro.gitpro']
    assert 'importlib.metadata' not in sys.modules
