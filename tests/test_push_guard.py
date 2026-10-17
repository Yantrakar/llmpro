"""The push guard (pushguard.py, `llmpro hook push-guard`), check.scan_texts, and the setup item of the secrets
plugin. Real git in temporary repos with a bare repo as the remote; core.hooksPath is set locally to a folder
holding a pre-push that runs `python -m llmpro hook push-guard`. No real gitpro and no real home.

Fake secrets are built at run time, so this file holds no string that looks like a real token."""
import io
import json
import multiprocessing
import os
import stat
import subprocess
import sys
import time

import pytest

from llmpro import check, cli, pushguard
from llmpro.store import MACHINE

TOKEN = ''.join(['ghp_', 'A1b2C3d4E5f6G7h8', 'I9j0K1l2M3n4O5p6Q7r8'])  # a GitHub token of the right shape
LEAK = ''.join(['Zq7!', 'kP2m', 'Xv9#', 'Lw4r'])  # a value that only appears in the raw session file


def sh(*args, cwd=None, env=None, check_=True):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=check_)


def put_session(store, home):
    """A raw session file holding LEAK, listed in the store's manifest."""
    raw = home / 'raw' / 's1.jsonl'
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps({'password': LEAK}) + '\n')
    row = {'machine': MACHINE, 'status': 'imported', 'source_path': str(raw), 'tool': 'claude-code',
           'kind': 'chat', 'original_id': 's1'}
    with open(os.path.join(store, 'manifest.jsonl'), 'w') as fh:
        fh.write(json.dumps(row) + '\n')


@pytest.fixture
def env(store, home, tmp_path, monkeypatch, onpath):
    monkeypatch.setenv('AGENTS_STORE', store)
    monkeypatch.delenv('LLMPRO_SKIP', raising=False)
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(tmp_path / 'gitconfig'))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    put_session(store, home)
    return dict(os.environ)


@pytest.fixture
def repo(env, tmp_path):
    """A work repo with a bare remote called origin and the guard as its pre-push hook."""
    remote = tmp_path / 'remote.git'
    sh('git', 'init', '-q', '--bare', '-b', 'main', str(remote))
    work = tmp_path / 'work'
    work.mkdir()
    sh('git', 'init', '-q', '-b', 'main', str(work))
    for k, v in (('user.name', 'test'), ('user.email', 'test@example.com'), ('core.hooksPath', str(tmp_path / 'hooks'))):
        sh('git', '-C', str(work), 'config', k, v)
    sh('git', '-C', str(work), 'remote', 'add', 'origin', str(remote))
    hook = tmp_path / 'hooks' / 'pre-push'
    hook.parent.mkdir()
    hook.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m llmpro hook push-guard "$@"\n')
    hook.chmod(0o755)
    return work


def commit(repo, path, text, msg='c'):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    sh('git', '-C', str(repo), 'add', '-A')
    sh('git', '-C', str(repo), 'commit', '-q', '-m', msg)
    return sh('git', '-C', str(repo), 'rev-parse', 'HEAD').stdout.strip()


def push(repo, *args, env=None):
    return sh('git', '-C', str(repo), 'push', '-q', *args, env=env, check_=False)


def test_known_format_is_stopped(repo, env):
    commit(repo, 'a.txt', 'one\n')
    sha = commit(repo, 'src/conf.txt', f'first\nsecond\nx = "{TOKEN}"\n')
    r = push(repo, 'origin', 'main', env=env)
    assert r.returncode != 0
    assert 'push stopped' in r.stderr
    assert f'  {sha[:7]} src/conf.txt:3 [github] ghp' in r.stderr
    assert TOKEN not in r.stderr and TOKEN not in r.stdout
    assert 'LLMPRO_SKIP=1 git push' in r.stderr


def test_value_from_raw_session_is_stopped(repo, env):
    sha = commit(repo, 'notes.txt', f'a\nb\nc\nvalue {LEAK}\n')
    r = push(repo, 'origin', 'main', env=env)
    assert r.returncode != 0
    assert f'{sha[:7]} notes.txt:4 [' in r.stderr
    assert LEAK not in r.stderr


def test_clean_push_is_silent(repo, env):
    commit(repo, 'a.txt', 'hello\n')
    r = push(repo, 'origin', 'main', env=env)
    assert r.returncode == 0
    assert r.stderr == '' and r.stdout == ''


def test_skip_lets_it_through(repo, env):
    commit(repo, 'a.txt', f'x = "{TOKEN}"\n')
    r = push(repo, 'origin', 'main', env={**env, 'LLMPRO_SKIP': '1'})
    assert r.returncode == 0 and r.stderr == ''


def test_new_branch_checks_only_new_commits(repo, env):
    commit(repo, 'a.txt', f'x = "{TOKEN}"\n')
    assert push(repo, 'origin', 'main', env={**env, 'LLMPRO_SKIP': '1'}).returncode == 0
    sh('git', '-C', str(repo), 'switch', '-q', '-c', 'feature')
    commit(repo, 'b.txt', 'clean\n')
    r = push(repo, '-u', 'origin', 'feature', env=env)
    assert r.returncode == 0 and r.stderr == ''
    # a bad commit on the new branch is still found
    commit(repo, 'c.txt', f'y = "{TOKEN}"\n')
    r = push(repo, 'origin', 'feature', env=env)
    assert r.returncode != 0 and 'c.txt:1' in r.stderr and 'a.txt' not in r.stderr


def test_deleted_branch_passes(repo, env):
    commit(repo, 'a.txt', 'ok\n')
    sh('git', '-C', str(repo), 'switch', '-q', '-c', 'feature')
    assert push(repo, '-u', 'origin', 'feature', env=env).returncode == 0
    r = push(repo, 'origin', '--delete', 'feature', env=env)
    assert r.returncode == 0 and r.stderr == ''


def test_allowlisted_fingerprint_passes(repo, env, store):
    os.makedirs(os.path.join(store, '.agentspro'))
    with open(os.path.join(store, '.agentspro', 'allowlist.json'), 'w') as fh:
        json.dump({check.fp(TOKEN): 'a test token'}, fh)
    commit(repo, 'a.txt', f'x = "{TOKEN}"\n')
    r = push(repo, 'origin', 'main', env=env)
    assert r.returncode == 0 and r.stderr == ''


def test_broken_store_fails_open(repo, env, store):
    os.remove(os.path.join(store, 'config.toml'))
    os.mkdir(os.path.join(store, 'config.toml'))  # exists, cannot be read
    commit(repo, 'a.txt', f'x = "{TOKEN}"\n')
    r = push(repo, 'origin', 'main', env=env)
    assert r.returncode == 0
    assert 'llmpro: push guard could not run:' in r.stderr and 'the push goes on' in r.stderr


def test_no_store_is_silent(repo, env, tmp_path):
    commit(repo, 'a.txt', f'x = "{TOKEN}"\n')
    r = push(repo, 'origin', 'main', env={**env, 'AGENTS_STORE': str(tmp_path / 'nothing')})
    assert r.returncode == 0 and r.stderr == ''


def test_from_the_store_itself(store, monkeypatch, env):
    monkeypatch.chdir(store)
    monkeypatch.setattr(check, 'scan_texts', lambda *a, **k: pytest.fail('scanned'))
    line = f'refs/heads/main {"1" * 40} refs/heads/main {"0" * 40}\n'
    assert pushguard.run([], io.StringIO(line)) == 0


def test_more_than_the_cap_says_so(repo, env, monkeypatch, capsys):
    monkeypatch.setattr(pushguard, 'MAX_COMMITS', 2)
    for i in range(4):
        sha = commit(repo, f'f{i}.txt', f'{i}\n')
    monkeypatch.chdir(repo)
    line = f'refs/heads/main {sha} refs/heads/main {"0" * 40}\n'
    assert pushguard.run([], io.StringIO(line)) == 0
    assert 'checking the newest 2' in capsys.readouterr().err


def test_added_texts_maps_lines(repo, env, monkeypatch):
    monkeypatch.chdir(repo)
    commit(repo, 'a.txt', 'l1\nl2\nl3\nl4\n')
    sha = commit(repo, 'a.txt', 'l1\nNEW\nl3\nl4\nEND\n')
    texts, where = pushguard.added_texts([sha])
    label = f'{sha[:7]} a.txt'
    assert texts[label] == 'NEW\nEND' and where[label][2] == [2, 5]


def test_scan_texts_returns_data_and_prints_nothing(store, home, env, capsys):
    texts = {'one': f'a\nx = "{TOKEN}"\n', 'two': f'p\nq\nvalue {LEAK}\n', 'three': 'nothing here\n'}
    hits = check.scan_texts(store, texts)
    cap = capsys.readouterr()
    assert cap.out == '' and cap.err == ''
    by = {h.label: h for h in hits}
    assert set(by) == {'one', 'two'}
    assert by['one'].how == 'pattern' and by['one'].kind == 'github'
    assert check.line_of(texts['one'], by['one'].offset) == 2
    assert by['two'].how in ('whole', 'piece') and check.line_of(texts['two'], by['two'].offset) == 3
    for h in hits:
        assert TOKEN not in h.masked and LEAK not in h.masked
    assert not os.path.exists(os.path.join(store, '.git', 'agentspro', 'blobs.json'))


def _scan_in_child(store, q):
    q.put([h.label for h in check.scan_texts(store, {'t': f'value {LEAK}\n'}, jobs=1)])


def test_scan_texts_jobs_one_works_in_a_daemon_process(store, home, env):
    ctx = multiprocessing.get_context('fork')
    q = ctx.Queue()
    p = ctx.Process(target=_scan_in_child, args=(store, q), daemon=True)
    p.start()
    p.join(30)
    assert p.exitcode == 0 and q.get(timeout=5) == ['t']


# What `llmpro check` printed on this fixture store before scan_texts existed (recorded from the earlier code).
CHECK_BEFORE = """store health: 0 problems
pattern scan: 4 files, 0 hits
leak check: 1 raw sources (1 read, the rest from the cache), 1 distinct secret values, 9 8-character pieces
leak check: 0 hits
passed
"""


def test_check_output_is_unchanged(store, home, env, capsys):
    (home / '.agents' / 'notes.txt').write_text('nothing secret\n')
    assert cli.main(['check', '--store', store]) == 0
    assert capsys.readouterr().out == CHECK_BEFORE
    (home / '.agents' / 'bad.txt').write_text(f'oops {LEAK}\n')
    assert cli.main(['check', '--store', store]) == 1
    out = capsys.readouterr().out
    assert out.count('LEAK bad.txt:1 whole [json-field]') == 1 and LEAK not in out


def test_guard_is_fast_on_a_small_push(repo, env, monkeypatch):
    commit(repo, 'a.txt', 'one\n')
    sha = commit(repo, 'b.txt', 'two\n')
    line = f'refs/heads/main {sha} refs/heads/main {"0" * 40}\n'
    monkeypatch.chdir(repo)
    pushguard.run([], io.StringIO(line))  # fills the cache
    t = time.perf_counter()
    assert pushguard.run([], io.StringIO(line)) == 0
    took = time.perf_counter() - t
    print(f'GUARD_SECONDS {took:.3f}')
    assert took < 2


# ---- the setup item ------------------------------------------------------------------------------------

@pytest.fixture
def guard(fake_gitpro, machine, store, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(machine / 'data'))
    from llmpro import gitpro, plugins
    gitpro.reset_cache()
    plugins.reset_cache()

    def status(installed='global', chain_to=None):
        fake_gitpro.answer(['guard', 'status', '--json'], json.dumps(
            {'ok': True, 'command': 'guard', 'installed': installed, 'hooks_dir': '/x', 'chain_to': chain_to}))
    status.script = machine / 'data' / 'llmpro' / 'hooks' / 'pre-push'
    return status


def dry(store, capsys):
    code = cli.main(['setup', '-n', '--only', 'secrets', '--store', store])
    return code, capsys.readouterr().out


def test_item_shows_lines_and_never_writes_gitpro_toml(guard, store, machine, capsys):
    guard()
    code, out = dry(store, capsys)
    assert code == 1
    assert 'push guard' in out and 'script missing' in out
    assert '[guard]' in out and 'chain_to = "~/.local/share/llmpro/hooks"' in out
    assert not (machine / '.config' / 'gitpro.toml').exists()


def test_item_in_place(guard, store, machine, capsys):
    guard(chain_to=str(guard.script.parent))
    assert cli.main(['setup', '-y', '--only', 'secrets', '--store', store]) == 0
    capsys.readouterr()
    code, out = dry(store, capsys)
    assert code == 0 and 'in place' in out


def test_item_other_folder(guard, store, machine, capsys):
    guard(chain_to='~/elsewhere')
    cli.main(['setup', '-y', '--only', 'secrets', '--store', store])
    capsys.readouterr()
    code, out = dry(store, capsys)
    assert code == 0 and 'chain_to is ~/elsewhere' in out
    assert 'call llmpro hook push-guard from it' in out


def test_item_guard_not_installed(guard, store, capsys):
    guard(installed='none')
    cli.main(['setup', '-y', '--only', 'secrets', '--store', store])
    capsys.readouterr()
    code, out = dry(store, capsys)
    assert code == 0 and 'gitpro guard not installed' in out and 'run llmpro setup again after gitpro setup' in out


def test_item_hidden_without_guard_capability(fake_gitpro, store, machine, capsys):
    fake_gitpro.version(capabilities=('setup',))
    code, out = dry(store, capsys)
    assert 'push guard' not in out


def test_setup_writes_script_0755_and_undo_removes_it(guard, store, machine, capsys):
    guard()
    assert cli.main(['setup', '-y', '--only', 'secrets', '--store', store]) == 0
    cap = capsys.readouterr()
    script = guard.script
    assert script.read_text() == pushguard.SCRIPT
    assert stat.S_IMODE(script.stat().st_mode) == 0o755
    assert 'chain_to = "~/.local/share/llmpro/hooks"' in cap.err
    assert not (machine / '.config' / 'gitpro.toml').exists()
    undo = cap.out.split('To undo: sh ')[1].split()[0]
    sh('sh', undo)
    assert not script.exists()


def test_undo_restores_an_earlier_script(guard, store, machine, capsys):
    guard()
    guard.script.parent.mkdir(parents=True)
    guard.script.write_text('#!/bin/sh\necho mine\n')
    guard.script.chmod(0o700)
    cli.main(['setup', '-y', '--only', 'secrets', '--store', store])
    undo = capsys.readouterr().out.split('To undo: sh ')[1].split()[0]
    sh('sh', undo)
    assert guard.script.read_text() == '#!/bin/sh\necho mine\n'
    assert stat.S_IMODE(guard.script.stat().st_mode) == 0o700


def test_uninstall_removes_script_and_undo_puts_it_back(guard, store, machine, capsys):
    guard(chain_to=str(guard.script.parent))
    cli.main(['setup', '-y', '--only', 'secrets', '--store', store])
    capsys.readouterr()
    assert cli.main(['uninstall', '-n', '--store', store]) == 1
    assert 'chain_to line in ~/.config/gitpro.toml can go too' in capsys.readouterr().out
    assert cli.main(['uninstall', '-y', '--store', store]) == 0
    cap = capsys.readouterr()
    assert not guard.script.exists()
    assert 'can go too' in cap.err
    sh('sh', cap.out.split('To undo: sh ')[1].split()[0])
    assert guard.script.read_text() == pushguard.SCRIPT
