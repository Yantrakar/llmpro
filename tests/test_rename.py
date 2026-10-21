"""The rename to llmpro (the old command is retired), the --version JSON and the JSON envelope."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import llmpro
from llmpro import cli, jsonout, mcp, setup, sync

OLD_HOOK = '#!/bin/sh\nexec agentspro hook {name} "$@"\n'
OLD_DRIVER = 'agentspro merge-driver manifest %O %A %B'
CAPABILITIES = ['check', 'check.json', 'find', 'find.json', 'mcp', 'resume', 'resume.json', 'resume.target',
                'setup', 'setup.dry-run', 'summary', 'sync', 'sync.dry-run', 'uninstall', 'version.json']
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
    assert got['capabilities'] == sorted(set(got['capabilities']))
    assert set(CAPABILITIES) <= set(got['capabilities'])  # plugins add theirs
    assert tuple(CAPABILITIES) == cli.CAPABILITIES


def test_envelope():
    assert jsonout.envelope('x', {'a': 1}, True) == {'ok': True, 'command': 'x', 'a': 1}
    assert jsonout.envelope('x', [1], False) == {'ok': False, 'command': 'x', 'results': [1]}


def test_the_old_command_is_gone():
    text = (SRC.parent.parent / 'pyproject.toml').read_text()
    scripts = text.split('[project.scripts]')[1].split('\n[')[0]
    assert [line.split('=')[0].strip() for line in scripts.strip().splitlines()] == ['llmpro']
    assert not hasattr(cli, 'alias_main')


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


def test_old_hooks_and_driver_are_missing(store, capsys):
    # they call `agentspro`, which no longer exists
    old_style(store)
    assert setup.git_settings_missing(store) == [*(f'hook {n}' for n in sync.HOOK_NAMES), 'merge.manifest.driver']
    code, got = run_json(['--store', store, 'setup', '-n', '--json'], capsys)
    item = next(i for i in got['items'] if i['name'] == 'git settings')
    assert item['present'] is False


def test_sync_rewrites_old_hooks_and_driver(store):
    old_style(store)
    sync.repair_git_settings(store)
    assert setup.git_settings_missing(store) == []
    assert (Path(sync.hooks_dir(store)) / 'pre-commit').read_text() == sync.hook_text('pre-commit')


def test_setup_rewrites_old_hooks_and_the_undo_puts_them_back(machine, store, capsys):
    old_style(store)
    undo = setup.Undo()
    setup.apply_settings(store, undo)
    assert setup.git_settings_missing(store) == []
    script = '\n'.join(undo.lines)
    assert 'exec agentspro hook pre-commit' in script and OLD_DRIVER in script


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
    ('cli.py', '.git/agentspro'),
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
