"""sync against a local bare repo as the hub, the version guard, and the manifest merge driver.
Hooks and the merge driver run `llmpro`, so it must be on PATH (the onpath fixture)."""
import json
import os
import subprocess

import pytest

import llmpro
from llmpro import cli, setup, store as cfg, sync


def git(d, *args, check=True):
    return subprocess.run(['git', '-C', str(d), *args], capture_output=True, text=True, check=check)


@pytest.fixture
def hub(tmp_path):
    p = tmp_path / 'hub.git'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(p)], check=True)
    return p


@pytest.fixture
def two(home, hub, onpath, monkeypatch, capsys):
    """Two clones of one store (a and b) with the hub as remote "hub"; both have synced once."""
    monkeypatch.setattr(setup, 'running_tools', lambda: [])
    monkeypatch.setenv('XDG_STATE_HOME', str(home / 'state'))
    a, b = home / 'a-store', home / 'b-store'
    assert cli.main(['setup', '-y', '--store', str(a)]) == 0
    for k, v in (('user.name', 'test'), ('user.email', 'test@example.com')):
        git(a, 'config', k, v)
    git(a, 'checkout', '-q', '-b', 'main')
    git(a, 'remote', 'add', 'hub', str(hub))
    code = cli.main(['sync', '--store', str(a)])
    err = capsys.readouterr().err
    assert code == 0, err
    assert git(hub, 'rev-parse', 'main', check=False).returncode == 0, err
    assert cli.main(['setup', '-y', '--hub', str(hub), '--store', str(b)]) == 0
    for k, v in (('user.name', 'test'), ('user.email', 'test@example.com')):
        git(b, 'config', k, v)
    capsys.readouterr()
    yield a, b
    cfg.LOCAL_ONLY[:] = []


def hub_path(store):
    return git(store, 'remote', 'get-url', 'hub').stdout.strip()


def sync_(store, capsys, *extra):
    code = cli.main(['sync', '-q', *extra, '--store', str(store)])
    return code, capsys.readouterr().err


def test_sync_pushes_and_the_other_clone_merges(two, capsys):
    a, b = two
    (b / 'note-b.md').write_text('from b\n')
    assert sync_(b, capsys)[0] == 0
    assert git(b, 'rev-parse', 'HEAD').stdout == git(hub_path(a), 'rev-parse', 'main').stdout
    assert sync_(a, capsys)[0] == 0
    assert (a / 'note-b.md').read_text() == 'from b\n'
    # and back: a writes, b receives
    (a / 'note-a.md').write_text('from a\n')
    assert sync_(a, capsys)[0] == 0
    assert sync_(b, capsys)[0] == 0
    assert (b / 'note-a.md').read_text() == 'from a\n'
    assert git(a, 'rev-parse', 'HEAD').stdout == git(b, 'rev-parse', 'HEAD').stdout


def test_diverged_clones_are_merged_and_pushed(two, capsys):
    a, b = two
    (a / 'one.md').write_text('1\n')
    (b / 'two.md').write_text('2\n')
    assert sync_(b, capsys)[0] == 0
    assert sync_(a, capsys)[0] == 0  # fetch, check, merge (a real merge commit), push
    assert (a / 'two.md').exists() and (a / 'one.md').exists()
    assert len(git(a, 'rev-list', '--merges', 'HEAD').stdout.split()) == 1
    assert git(a, 'rev-parse', 'HEAD').stdout == git(hub_path(a), 'rev-parse', 'main').stdout


def test_fetched_commit_with_a_secret_is_refused_and_not_merged(two, hub, capsys):
    a, b = two
    secret = 'Zq8vL2mWp' + 'x7Rt4Kd9'  # built at run time: no secret-looking text in this file
    (b / 'leak.md').write_text('password: "' + secret + '"\n')
    git(b, 'add', 'leak.md')
    git(b, 'commit', '-q', '--no-verify', '-m', 'bad')
    git(b, 'push', '-q', '--no-verify', 'hub', 'HEAD:main')
    head = git(a, 'rev-parse', 'HEAD').stdout
    code, err = sync_(a, capsys)
    assert code == 1
    assert 'not merged' in err and secret not in err
    assert git(a, 'rev-parse', 'HEAD').stdout == head
    assert not (a / 'leak.md').exists()


def test_sync_dry_run_changes_nothing(two, capsys):
    a, b = two
    (b / 'n.md').write_text('x\n')
    assert sync_(b, capsys)[0] == 0
    git(a, 'fetch', '-q', 'hub')
    (a / 'local.md').write_text('y\n')
    state = lambda: (git(a, 'rev-parse', 'HEAD').stdout, git(a, 'status', '--porcelain').stdout,
                     git(a, 'rev-parse', 'hub/main').stdout, open(a / 'config.toml').read())
    before = state()
    code = cli.main(['sync', '-n', '--store', str(a)])
    out = capsys.readouterr().out
    assert code == 0 and state() == before
    assert '0 commits ahead, 1 behind' in out  # the hub's commit; the local file is not committed yet


def test_sync_dry_run_json(two, capsys):
    a, _ = two
    assert cli.main(['sync', '-n', '--json', '--store', str(a)]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got['hub']['branch'] == 'main' and got['redact_version']['tool'] == llmpro.REDACT_VERSION
    assert got['git_settings_missing'] == []


def test_sync_dry_run_without_hub_or_fetch(store, capsys):
    assert cli.main(['sync', '-n', '--store', store]) == 0
    out = capsys.readouterr().out
    assert 'no hub remote' in out and 'repair' in out  # hooks are not there yet


def test_sync_dry_run_reports_new_and_changed_sessions(home, store, onpath, capsys):
    d = home / '.claude/projects/p'
    d.mkdir(parents=True)
    f = d / 's.jsonl'
    f.write_text(json.dumps({'type': 'user', 'sessionId': 's', 'timestamp': '2026-01-01T00:00:00Z',
                             'message': {'role': 'user', 'content': 'hello'}}) + '\n')
    assert cli.main(['sync', '-n', '--store', store]) == 0
    assert 'claude-code  1 new, 0 changed' in capsys.readouterr().out
    assert cli.main(['sync', '--local', '-q', '--store', store]) == 0
    capsys.readouterr()
    assert cli.main(['sync', '-n', '--store', store]) == 0
    assert 'claude-code  0 new, 0 changed' in capsys.readouterr().out
    f.write_text(f.read_text() * 2)
    assert cli.main(['sync', '-n', '--store', store]) == 0
    assert 'claude-code  0 new, 1 changed' in capsys.readouterr().out


# ---------------------------------------------------------------- version guard

def test_store_newer_than_tool_stops_sync(store, onpath, capsys):
    open(os.path.join(store, 'config.toml'), 'w').write(f'[store]\nredact_version = {llmpro.REDACT_VERSION + 1}\n')
    (open(os.path.join(store, 'x.md'), 'w')).write('x\n')
    code = cli.main(['sync', '--local', '--store', store])
    assert code == 2
    assert 'upgrade llmpro: the store was written by a newer redactor' in capsys.readouterr().err
    assert git(store, 'log', '--oneline', check=False).stdout == ''  # nothing committed
    assert cli.main(['sync', '-n', '--store', store]) == 2


def test_missing_version_is_written_in_the_sync_commit(store, onpath, capsys):
    cfgfile = os.path.join(store, 'config.toml')
    open(cfgfile, 'w').write('# mine\n[privacy]\nlocal_only = []\n')
    assert cli.main(['sync', '--local', '-q', '--store', store]) == 0
    text = open(cfgfile).read()
    assert text.startswith('# mine\n') and f'[store]\nredact_version = {llmpro.REDACT_VERSION}\n' in text
    assert 'config.toml' in git(store, 'show', '--name-only', '--format=', 'HEAD').stdout


def test_older_version_is_raised_and_other_keys_kept(store, onpath, capsys, monkeypatch):
    cfgfile = os.path.join(store, 'config.toml')
    open(cfgfile, 'w').write('[store]\nformat = 1\nredact_version = 1  # was\n\n[privacy]\nlocal_only = []\n')
    monkeypatch.setattr(llmpro, 'REDACT_VERSION', 3)
    assert cli.main(['sync', '--local', '-q', '--store', store]) == 0
    assert open(cfgfile).read() == '[store]\nformat = 1\nredact_version = 3\n\n[privacy]\nlocal_only = []\n'
    assert 'config.toml' in git(store, 'show', '--name-only', '--format=', 'HEAD').stdout
    # the same version again: no new commit
    n = git(store, 'rev-list', '--count', 'HEAD').stdout
    assert cli.main(['sync', '--local', '-q', '--store', store]) == 0
    assert git(store, 'rev-list', '--count', 'HEAD').stdout == n


@pytest.mark.parametrize('before, after', [
    ('', '[store]\nredact_version = 7\n'),
    ('[store]\nformat = 1\n', '[store]\nredact_version = 7\nformat = 1\n'),
    ('[a]\nb = 1', '[a]\nb = 1\n\n[store]\nredact_version = 7\n'),
])
def test_set_redact_version(tmp_path, before, after):
    p = tmp_path / 'config.toml'
    p.write_text(before)
    sync.set_redact_version(str(p), 7)
    assert p.read_text() == after


# ---------------------------------------------------------------- manifest merge driver

def row(machine, oid, status):
    return {'tool': 'codex', 'machine': machine, 'kind': 'conversation', 'source_path': f'/s/{oid}',
            'original_id': oid, 'mtime': '', 'size': 1, 'output': '', 'status': status}


def write_rows(path, rows):
    with open(path, 'w') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')


def read_rows(path):
    return {(r['machine'], r['original_id']): r['status'] for r in map(json.loads, open(path).read().splitlines())}


def test_merge_manifest_function(tmp_path, monkeypatch):
    from llmpro import hot
    monkeypatch.setattr(hot, 'MACHINE', 'm1')
    ours, theirs = tmp_path / 'ours', tmp_path / 'theirs'
    write_rows(ours, [row('m1', 'a', 'new'), row('m2', 'b', 'stale')])
    write_rows(theirs, [row('m1', 'a', 'old'), row('m2', 'b', 'fresh'), row('m3', 'c', 'theirs')])
    assert hot.merge_manifest(str(ours), str(theirs)) == 0
    assert read_rows(ours) == {('m1', 'a'): 'new', ('m2', 'b'): 'fresh', ('m3', 'c'): 'theirs'}


def test_manifest_driver_inside_a_real_git_merge(store, onpath, capsys):
    me = cfg.MACHINE
    open(os.path.join(store, '.gitattributes'), 'w').write('manifest.jsonl merge=manifest\n')
    path = os.path.join(store, 'manifest.jsonl')
    write_rows(path, [row(me, 'a', 'base'), row('other', 'b', 'base')])
    assert cli.main(['sync', '--local', '-q', '--store', store]) == 0  # sets the driver; commits (the import may touch the rows)
    git(store, 'add', '-A')
    git(store, 'commit', '-q', '-m', 'base', check=False)
    write_rows(path, [row(me, 'a', 'base'), row('other', 'b', 'base')])
    git(store, 'add', '-A')
    git(store, 'commit', '-q', '-m', 'base', check=False)
    main = git(store, 'branch', '--show-current').stdout.strip()
    git(store, 'checkout', '-q', '-b', 'theirs')
    write_rows(path, [row(me, 'a', 'base'), row('other', 'b', 'theirs-edit'), row('third', 'c', 'added')])
    git(store, 'commit', '-q', '-am', 'theirs')
    git(store, 'checkout', '-q', main)
    write_rows(path, [row(me, 'a', 'ours-edit'), row('other', 'b', 'base')])
    git(store, 'commit', '-q', '-am', 'ours')
    merged = git(store, 'merge', '-q', '--no-edit', 'theirs', check=False)
    assert merged.returncode == 0, merged.stdout + merged.stderr
    assert read_rows(path) == {(me, 'a'): 'ours-edit', ('other', 'b'): 'theirs-edit', ('third', 'c'): 'added'}
