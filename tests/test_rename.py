"""The rename to llmpro, the alias, the --version JSON and the JSON envelope."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import llmpro
from llmpro import cli, hot, jsonout, mcp, setup, sync

OLD_HOOK = '#!/bin/sh\nexec agentspro hook {name} "$@"\n'
OLD_DRIVER = 'agentspro merge-driver manifest %O %A %B'
CAPABILITIES = ['check', 'check.json', 'find', 'find.json', 'mcp', 'setup', 'setup.dry-run', 'summary', 'sync',
                'sync.dry-run', 'uninstall', 'version.json']
SRC = Path(llmpro.__file__).parent


def run_json(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


def old_style(store):
    """Make the hooks and the merge driver of a store say `agentspro`."""
    sync.repair_git_settings(store)
    for name in sync.HOOK_NAMES:
        path = Path(sync.hooks_dir(store)) / name
        path.write_text(OLD_HOOK.format(name=name))
        path.chmod(0o755)
    subprocess.run(['git', '-C', store, 'config', '--local', 'merge.manifest.driver', OLD_DRIVER], check=True)


def test_package_name():
    assert llmpro.__version__
    here = SRC.parent
    assert not (here / 'agentspro').exists()
    spec = importlib.util.find_spec('agentspro')
    assert spec is None or str(here) not in str(spec.origin)


def test_version_text(capsys):
    assert cli.main(['--version']) == 0
    assert capsys.readouterr().out.strip() == f'llmpro {llmpro.__version__}'


def test_version_json_needs_no_store(home, capsys):
    code, got = run_json(['--version', '--json'], capsys)
    assert code == 0 and got['ok'] is True and got['command'] == 'version'
    assert got['version'] == llmpro.__version__ and got['python']
    assert got['capabilities'] == CAPABILITIES == sorted(got['capabilities'])
    assert tuple(CAPABILITIES) == cli.CAPABILITIES


def test_envelope():
    assert jsonout.envelope('x', {'a': 1}, True) == {'ok': True, 'command': 'x', 'a': 1}
    assert jsonout.envelope('x', [1], False) == {'ok': False, 'command': 'x', 'results': [1]}


def test_alias_prints_note_once(monkeypatch, capsys):
    monkeypatch.delenv('LLMPRO_NO_RENAME_NOTE', raising=False)
    monkeypatch.setattr(sys, 'argv', ['agentspro', '--version'])
    assert cli.alias_main() == 0
    captured = capsys.readouterr()
    assert captured.err == 'agentspro is now llmpro; this name keeps working for now\n'
    assert captured.out.strip() == f'llmpro {llmpro.__version__}'


def test_alias_note_can_be_silenced(monkeypatch, capsys):
    monkeypatch.setenv('LLMPRO_NO_RENAME_NOTE', '1')
    assert cli.alias_main(['--version']) == 0
    assert capsys.readouterr().err == ''


@pytest.mark.parametrize('first', ['hook', 'merge-driver'])
def test_alias_is_silent_for_hooks(first, monkeypatch, capsys):
    monkeypatch.delenv('LLMPRO_NO_RENAME_NOTE', raising=False)
    calls = []
    monkeypatch.setattr(hot, 'main', lambda argv: calls.append(argv) or 0)
    assert cli.alias_main([first, 'x']) == 0
    assert calls == [[first, 'x']]
    assert capsys.readouterr().err == ''


def test_json_outputs_have_the_envelope(store, capsys):
    code, got = run_json(['--store', store, 'check', '--json'], capsys)
    assert got['command'] == 'check' and got['ok'] is (code == 0)
    assert {'passed', 'exit', 'output'} <= set(got)

    code, got = run_json(['--store', store, 'check', '--near-miss', '--json'], capsys)
    assert got['command'] == 'check' and got['ok'] is True and 'output' in got

    code, got = run_json(['--store', store, 'find', '--json', 'nothing-matches-this'], capsys)
    assert got['command'] == 'find' and got['ok'] is True and got['results'] == []

    code, got = run_json(['--store', store, 'setup', '-n', '--json'], capsys)
    assert got['command'] == 'setup' and {'items', 'missing'} <= set(got)
    assert got['ok'] is (not got['missing']) and code == (1 if got['missing'] else 0)

    code, got = run_json(['--store', store, 'uninstall', '-n', '--json'], capsys)
    assert got['command'] == 'uninstall' and 'todo' in got and got['ok'] is (not got['todo'])

    code, got = run_json(['--store', store, 'sync', '-n', '--json'], capsys)
    assert got['command'] == 'sync' and got['ok'] is True
    assert {'redact_version', 'git_settings_missing', 'sessions', 'hub', 'unread_sources'} <= set(got)


def test_old_hooks_and_driver_are_not_missing(store, capsys):
    old_style(store)
    assert setup.git_settings_missing(store) == []
    code, got = run_json(['--store', store, 'setup', '-n', '--json'], capsys)
    item = next(i for i in got['items'] if i['name'] == 'git settings')
    assert item['present'] is True
    sync.repair_git_settings(store)  # does not rewrite them
    assert (Path(sync.hooks_dir(store)) / 'pre-commit').read_text() == OLD_HOOK.format(name='pre-commit')


def test_uninstall_removes_old_hooks_and_driver(machine, store, capsys):
    old_style(store)
    assert cli.main(['--store', store, 'uninstall', '-y']) == 0
    for name in sync.HOOK_NAMES:
        assert not (Path(sync.hooks_dir(store)) / name).exists()
    got = subprocess.run(['git', '-C', store, 'config', '--local', '--get', 'merge.manifest.driver'],
                         capture_output=True, text=True)
    assert got.returncode != 0


def test_new_hooks_say_llmpro(store):
    sync.repair_git_settings(store)
    for name in sync.HOOK_NAMES:
        assert (Path(sync.hooks_dir(store)) / name).read_text() == f'#!/bin/sh\nexec llmpro hook {name} "$@"\n'
    assert sync.MERGE_DRIVER == 'llmpro merge-driver manifest %O %A %B'
    assert sync.OLD_MERGE_DRIVER == OLD_DRIVER
    assert sync.OLD_HOOK_TEXT.format(name='pre-push') == OLD_HOOK.format(name='pre-push')


def test_state_dir(home, monkeypatch):
    monkeypatch.delenv('XDG_STATE_HOME', raising=False)
    assert setup.state_dir().endswith('/llmpro')
    assert setup.OLD_STATE_DIR_NAME == 'agentspro'
    new, old = setup.state_dirs()
    assert new.endswith('/llmpro') and old.endswith('/agentspro')


def test_mcp_server_name(store):
    reply = mcp.handle(store, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
    assert reply['result']['serverInfo']['name'] == 'llmpro'


# The only lines under src/llmpro that may say "agentspro": (file, text the line must contain).
ALLOWED = [
    ('cli.py', 'agentspro is now llmpro'),
    ('cli.py', 'alias_main'),
    ('cli.py', '.git/agentspro'),
    ('cli.py', 'The `agentspro` command'),
    ('check.py', '.agentspro/allowlist.json'),
    ('check.py', '.git/agentspro'),
    ('check.py', "'agentspro')"),
    ('importer/all.py', "'agentspro'"),
    ('importer/memory.py', '.agentspro/memory_overrides.json'),
    ('store.py', '.agentspro'),
    ('sync.py', 'OLD_'),
    ('setup.py', 'OLD_STATE_DIR_NAME'),
    ('templates/FORMAT.md', '.agentspro/'),
    ('templates/AGENTS.md', '.agentspro/'),
]


def test_no_stray_agentspro_in_source():
    stray = []
    for path in sorted(SRC.rglob('*')):
        if path.suffix not in ('.py', '.md', '.toml'):
            continue
        rel = path.relative_to(SRC).as_posix()
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.search('agentspro', line, re.I) and not any(rel == f and part in line for f, part in ALLOWED):
                stray.append(f'{rel}:{number}: {line.strip()}')
    assert stray == []
