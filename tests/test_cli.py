"""Flags and exit codes of the agentspro command."""
import json
import os
import subprocess
import sys

from agentspro import cli


def test_no_command_is_usage_error(capsys):
    assert cli.main([]) == 2


def test_unknown_flag_exits_2(capsys):
    assert cli.main(['sync', '--nope']) == 2


def test_sync_rejects_dry_run_for_now(capsys):
    assert cli.main(['sync', '-n']) == 2


def test_check_passes_on_empty_store(store, capsys):
    assert cli.main(['check', '--store', store]) == 0
    assert 'passed' in capsys.readouterr().out


def test_check_json(store, capsys):
    assert cli.main(['check', '--json', '--store', store]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got['passed'] is True and got['exit'] == 0


def test_check_quiet_prints_nothing_when_passed(store, capsys):
    assert cli.main(['check', '-q', '--store', store]) == 0
    assert capsys.readouterr().out == ''


def test_check_finds_a_secret(store, capsys):
    with open(os.path.join(store, 'note.md'), 'w') as fh:
        fh.write('password: "' + 'Zq8vL2mWp' + 'x7Rt4Kd9"\n')
    assert cli.main(['check', '--no-cache', '--store', store]) == 1
    out = capsys.readouterr().out
    assert 'FAILED' in out and 'Zq8vL2mWp' not in out


def test_store_from_environment(store, monkeypatch, capsys):
    monkeypatch.setenv('AGENTS_STORE', store)
    assert cli.main(['check']) == 0


def test_summary_needs_arguments(store, capsys):
    assert cli.main(['summary', '--store', store]) == 2


def test_sync_local_commits_and_installs_hooks(store, capsys, monkeypatch):
    monkeypatch.setenv('PATH', os.path.dirname(sys.executable) + os.pathsep + os.environ['PATH'])
    with open(os.path.join(store, 'notes.md'), 'w') as fh:
        fh.write('hello\n')
    assert cli.main(['sync', '--local', '-q', '--store', store]) == 0
    hook = os.path.join(store, '.git', 'hooks', 'pre-commit')
    assert open(hook).read() == '#!/bin/sh\nexec agentspro hook pre-commit "$@"\n'
    assert os.access(hook, os.X_OK)
    drv = subprocess.run(['git', '-C', store, 'config', 'merge.manifest.driver'], capture_output=True, text=True).stdout
    assert drv.strip() == 'agentspro merge-driver manifest %O %A %B'


def test_sync_refuses_store_inside_another_repo(store, tmp_path, capsys):
    inner = os.path.join(store, 'inner')
    os.mkdir(inner)
    open(os.path.join(inner, 'config.toml'), 'w').write('')
    assert cli.main(['sync', '--local', '--store', inner]) == 2


def test_hook_usage_error():
    assert cli.main(['hook', 'bogus']) == 2


def test_module_entry_point(store):
    r = subprocess.run([sys.executable, '-m', 'agentspro', 'check', '--store', store], capture_output=True, text=True)
    assert r.returncode == 0
