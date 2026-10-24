"""The push guard (pushguard.py, `llmpro hook push-guard`), check.scan_texts, and the setup item of the secrets
plugin. Real git in temporary repos with a bare repo as the remote; core.hooksPath is set locally to a folder
holding a pre-push that runs `python -m llmpro hook push-guard`. No real gitpro and no real home.

Fake secrets are built at run time, so this file holds no string that looks like a real token."""
import base64
import hashlib
import inspect
import io
import json
import multiprocessing
import os
import stat
import subprocess
import sys
import time

import pytest

import llmpro
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


def test_push_from_a_bare_repo_is_checked(repo, env, tmp_path):
    commit(repo, 'a.txt', f'x = "{TOKEN}"\n')
    bare = tmp_path / 'mirror.git'
    sh('git', 'clone', '-q', '--bare', str(repo), str(bare))
    sh('git', '-C', str(bare), 'config', 'core.hooksPath', str(tmp_path / 'hooks'))
    sh('git', 'init', '-q', '--bare', '-b', 'main', str(tmp_path / 'other.git'))
    r = push(bare, str(tmp_path / 'other.git'), 'main', env=env)
    assert r.returncode != 0
    assert 'push stopped' in r.stderr and 'a.txt:1' in r.stderr and 'could not run' not in r.stderr


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


# ---- the leak cache across releases, and the guard's time budget (spec 22) -----------------------------
# sha256 of the code that decides what is collected and how it is hashed, for each RULES_VERSION.
RULES_PIN = {5: 'd592ead68b4e6520e9eadb62a556034965476446b7909f1f6c05503a70998650'}


def rules_code_hash():
    src = inspect.getsource(check)
    parts = [src[src.index('# ---- part 1 patterns'):src.index('THIS_MACHINE = MACHINE')]]
    parts += [inspect.getsource(f) for f in (check.public_prefix, check.piece_ok, check.hashed_entry)]
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()


def test_collection_code_is_pinned_to_its_rules_version():
    assert rules_code_hash() == RULES_PIN.get(check.RULES_VERSION), (
        'The code that decides what the leak check collects changed. If the change alters what is collected, '
        'raise check.RULES_VERSION (the cache is then collected again); either way record the new hash here: '
        f'RULES_PIN[{check.RULES_VERSION}] = {rules_code_hash()!r}')


def test_the_cache_is_keyed_on_the_rules_not_the_release():
    assert check.CACHE_VERSION == f'rules{check.RULES_VERSION}'
    assert llmpro.__version__ not in check.CACHE_VERSION


def test_a_cache_of_other_rules_is_dropped(store, env, monkeypatch):
    cdir = check.cache_dir(store)
    _, cache = check.load_cache(cdir)
    cache['sources'] = {'x': {'sig': [1], 'offset': None, 'tail': None, 'values': []}}
    check.save_cache(cdir, cache)
    assert check.load_cache(cdir)[1]['sources']
    monkeypatch.setattr(check, 'CACHE_VERSION', f'rules{check.RULES_VERSION + 1}')
    assert check.load_cache(cdir)[1]['sources'] == {}


def long_session(home, n=40):
    """The raw session file again, with LEAK only on its last line."""
    raw = home / 'raw' / 's1.jsonl'
    raw.write_text(''.join(json.dumps({'text': f'line {i}'}) + '\n' for i in range(n))
                   + json.dumps({'password': LEAK}) + '\n')
    return raw


def cached(store, raw):
    cache = json.load(open(os.path.join(check.cache_dir(store), 'cache.json'), encoding='utf-8'))
    return cache['sources'].get(str(raw))


def test_no_time_left_checks_what_is_cached_and_says_so(repo, env, store, home, monkeypatch, capsys):
    raw = long_session(home)
    sha = commit(repo, 'a.txt', f'value {LEAK}\n')
    line = f'refs/heads/main {sha} refs/heads/main {"0" * 40}\n'
    monkeypatch.chdir(repo)
    monkeypatch.setattr(pushguard, 'BUDGET', 0)
    assert pushguard.run([], io.StringIO(line)) == 0
    err = capsys.readouterr().err
    assert 'no time to read 1 raw logs in full' in err and 'llmpro check' in err and LEAK not in err
    assert cached(store, raw) is None  # not started, so nothing cached for it
    monkeypatch.setattr(pushguard, 'BUDGET', 20)
    assert pushguard.run([], io.StringIO(line)) == 1
    err = capsys.readouterr().err
    assert 'push stopped' in err and 'no time' not in err and LEAK not in err


def test_no_time_left_still_stops_a_known_format(repo, env, home, monkeypatch, capsys):
    long_session(home)
    sha = commit(repo, 'a.txt', f'x = "{TOKEN}"\n')
    monkeypatch.chdir(repo)
    monkeypatch.setattr(pushguard, 'BUDGET', 0)
    assert pushguard.run([], io.StringIO(f'refs/heads/main {sha} refs/heads/main {"0" * 40}\n')) == 1
    err = capsys.readouterr().err
    assert 'push stopped' in err and '[github]' in err and 'no time' in err and TOKEN not in err


class Clock:
    """time for check.py whose monotonic() moves one second each time it is read."""
    def __init__(self):
        self.t = 0

    def monotonic(self):
        self.t += 1
        return self.t


def test_a_log_cut_short_is_cached_part_way_and_read_on_next_time(store, home, env, monkeypatch):
    raw = long_session(home)
    monkeypatch.setattr(check, 'time', Clock())
    late = []
    assert check.scan_texts(store, {'t': f'value {LEAK}\n'}, deadline=4, late=late) == []
    assert late == [str(raw)]
    entry = cached(store, raw)
    assert 0 < entry['offset'] < raw.stat().st_size and entry['sig'][1] == entry['offset']
    late = []
    hits = check.scan_texts(store, {'t': f'value {LEAK}\n'}, late=late)
    assert [h.label for h in hits] == ['t'] and late == []
    assert cached(store, raw)['offset'] == raw.stat().st_size


def test_the_fewest_bytes_are_read_first_under_a_deadline(store, home, env, monkeypatch):
    big = long_session(home, n=400)
    small = home / 'raw' / 's2.jsonl'
    small.write_text(json.dumps({'password': LEAK}) + '\n')
    rows = [{'machine': MACHINE, 'status': 'imported', 'source_path': str(p), 'tool': 'claude-code', 'kind': 'chat',
             'original_id': p.stem} for p in (big, small)]
    with open(os.path.join(store, 'manifest.jsonl'), 'w') as fh:
        fh.write(''.join(json.dumps(r) + '\n' for r in rows))
    monkeypatch.setattr(check, 'time', Clock())
    late = []
    hits = check.scan_texts(store, {'t': f'value {LEAK}\n'}, deadline=4, late=late)
    assert [h.label for h in hits] == ['t'] and late == [str(big)]



def test_check_budget_reports_raw_logs_not_read(store, home, env, capsys):
    raw = long_session(home)
    assert cli.main(['check', '--store', store, '--json', '--budget', '0']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['late'] == 1 and cached(store, raw) is None
    assert any('1 raw logs not read in full in the time' in l and 'llmpro check' in l for l in out['output'])
    assert cli.main(['check', '--store', store, '--json']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['late'] == 0 and not any('not read in full' in l for l in out['output'])
    assert cached(store, raw) is not None


def test_check_budget_needs_the_cache(store, env, capsys):
    assert cli.main(['check', '--store', store, '--budget', '5', '--no-cache']) == 2
    assert '--no-cache' in capsys.readouterr().err

# ---- the setup item ------------------------------------------------------------------------------------

@pytest.fixture
def guard(fake_gitpro, machine, store, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(machine / 'data'))
    from llmpro import gitpro, plugins
    gitpro.reset_cache()
    plugins.reset_cache()

    monkeypatch.delenv('GITPRO_CONFIG', raising=False)

    def status(installed='global', chain_to=None):
        fake_gitpro.answer(['guard', 'status', '--json'], json.dumps(
            {'ok': True, 'command': 'guard', 'installed': installed, 'hooks_dir': '/x', 'chain_to': chain_to}))
    status.script = machine / 'data' / 'llmpro' / 'hooks' / 'pre-push'
    return status


def dry(store, capsys):
    code = cli.main(['setup', '-n', '--only', 'secrets', '--store', store])
    return code, capsys.readouterr().out


def test_no_gitpro_config_shows_the_lines_to_add_and_writes_nothing(guard, store, machine, capsys):
    guard()
    code, out = dry(store, capsys)
    assert code == 1
    assert 'push guard' in out and 'script missing' in out
    assert '[guard]' in out and 'chain_to = "~/data/llmpro/hooks"' in out
    assert '~/.config/gitpro.toml does not exist' in out
    assert not (machine / '.config' / 'gitpro.toml').exists()
    assert 'then run llmpro setup again (it runs gitpro setup for you)' in out and 'run gitpro setup' not in out


def test_apply_says_to_run_llmpro_setup_not_gitpro_setup(guard, store, machine, capsys):
    guard()
    assert cli.main(['setup', '-y', '--only', 'secrets', '--store', store]) == 0
    err = capsys.readouterr().err
    assert 'For you to do by hand:' in err and 'then run llmpro setup again (it runs gitpro setup for you):' in err
    assert 'run: gitpro setup' not in err


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
    assert code == 0 and 'gitpro guard not installed' in out and 'llmpro setup runs gitpro setup, which installs it' in out


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
    assert 'chain_to = "~/data/llmpro/hooks"' in cap.err
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
    assert 'points at this folder, but llmpro did not write it; take it out by hand' in capsys.readouterr().out
    assert cli.main(['uninstall', '-y', '--store', store]) == 0
    cap = capsys.readouterr()
    assert not guard.script.exists()
    assert 'take it out by hand' in cap.err
    sh('sh', cap.out.split('To undo: sh ')[1].split()[0])
    assert guard.script.read_text() == pushguard.SCRIPT


def test_setup_in_place_still_prints_the_lines_to_add(guard, store, machine, capsys):
    guard()
    assert cli.main(['setup', '-y', '--only', 'secrets', '--store', store]) == 0
    capsys.readouterr()
    assert cli.main(['setup', '-y', '--only', 'secrets', '--store', store]) == 0
    cap = capsys.readouterr()
    assert 'everything setup can do is in place' in cap.out
    assert 'For you to do by hand:' in cap.err and 'chain_to = "~/data/llmpro/hooks"' in cap.err
    assert not (machine / '.config' / 'gitpro.toml').exists()


CONFIG = """# my identities
[default]
name = "ss"
email = "ss@example.com"

[[identity]]
id = "work"
host = "github.com"
"""
DO = ['setup', '-y', '--agent', 'none']


def put_config(machine, text=CONFIG):
    path = machine / '.config' / 'gitpro.toml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)
    return path


def undo_of(out):
    return out.split('To undo: sh ')[1].split()[0]


def test_preview_says_it_adds_chain_to_and_rebuilds_the_hooks(guard, store, machine, capsys):
    guard()
    put_config(machine)
    code, out = dry(store, capsys)
    assert code == 1
    assert 'add chain_to = "~/data/llmpro/hooks" to the [guard] table of ~/.config/gitpro.toml (a copy is kept)' in out
    assert 'run gitpro setup -y --agent none, so its hooks run the push guard' in out


def test_setup_adds_chain_to_and_rebuilds_gitpro_hooks(guard, store, machine, fake_gitpro, capsys):
    import tomllib
    guard()
    fake_gitpro.answer(DO, '')
    path = put_config(machine)
    assert cli.main(['setup', '-y', '--only', 'secrets', '--store', store]) == 0
    cap = capsys.readouterr()
    text = path.read_text()
    assert text.startswith(CONFIG) and text.endswith('\n[guard]\nchain_to = "~/data/llmpro/hooks"\n')
    assert tomllib.loads(text)['guard'] == {'chain_to': '~/data/llmpro/hooks'}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (machine / '.config' / 'gitpro.toml.before-llmpro').read_text() == CONFIG
    assert DO in fake_gitpro.calls()
    assert 'added chain_to = "~/data/llmpro/hooks" to ~/.config/gitpro.toml' in cap.err
    assert 'llmpro does not write that file' not in cap.err
    sh('sh', undo_of(cap.out))
    assert path.read_text() == CONFIG and not guard.script.exists()
    assert fake_gitpro.calls().count(DO) == 2


def test_a_guard_table_gets_the_line_under_its_header(guard, store, machine, fake_gitpro, capsys):
    guard()
    fake_gitpro.answer(DO, '')
    path = put_config(machine, CONFIG + '\n[guard]\non_mismatch = "warn"\n')
    assert cli.main(['setup', '-y', '--only', 'secrets', '--store', store]) == 0
    assert path.read_text() == CONFIG + '\n[guard]\nchain_to = "~/data/llmpro/hooks"\non_mismatch = "warn"\n'


def test_guard_not_installed_yet_is_done_in_the_same_run(guard, store, machine, fake_gitpro, capsys):
    guard(installed='none')
    fake_gitpro.answer(DO, '')
    path = put_config(machine)
    assert cli.main(['setup', '-y', '--only', 'secrets', '--store', store]) == 0
    assert 'chain_to = "~/data/llmpro/hooks"' in path.read_text()
    assert DO in fake_gitpro.calls()


def test_a_config_llmpro_cannot_edit_is_left_with_the_lines(guard, store, machine, fake_gitpro, capsys):
    guard()
    path = put_config(machine, '[guard\n')
    code, out = dry(store, capsys)
    assert ('~/.config/gitpro.toml is not valid TOML' in out and 'llmpro left it. Add these lines to it by hand, '
            'then run llmpro setup again (it runs gitpro setup for you):' in out)
    assert '[guard]' in out and 'chain_to = "~/data/llmpro/hooks"' in out
    assert cli.main(['setup', '-y', '--only', 'secrets', '--store', store]) == 0
    cap = capsys.readouterr()
    assert path.read_text() == '[guard\n' and DO not in fake_gitpro.calls()
    assert 'For you to do by hand:' in cap.err and 'is not valid TOML' in cap.err


def test_a_hook_folder_others_can_write_is_made_private(guard, store, machine, fake_gitpro, capsys):
    guard(chain_to=str(guard.script.parent))
    put_config(machine)
    guard.script.parent.mkdir(parents=True)
    guard.script.parent.chmod(0o775)
    code, out = dry(store, capsys)
    assert code == 1 and 'chmod go-w ~/data/llmpro/hooks (gitpro refuses a chain_to folder others can write)' in out
    assert cli.main(['setup', '-y', '--only', 'secrets', '--store', store]) == 0
    assert stat.S_IMODE(guard.script.parent.stat().st_mode) == 0o755
    sh('sh', undo_of(capsys.readouterr().out))
    assert stat.S_IMODE(guard.script.parent.stat().st_mode) == 0o775


def test_gitpro_rebuild_failure_says_what_next(guard, store, machine, fake_gitpro, capsys):
    guard()
    fake_gitpro.answer(DO, '', exit=4)
    path = put_config(machine)
    assert cli.main(['setup', '-y', '--only', 'secrets', '--store', store]) == 2
    err = capsys.readouterr().err
    assert 'gitpro setup -y --agent none failed with exit status 4; its output above says why' in err
    assert 'To take back what this run changed instead: sh ' in err
    assert 'chain_to' in path.read_text()


def test_uninstall_takes_out_its_chain_to_line(guard, store, machine, fake_gitpro, capsys):
    guard(chain_to=str(guard.script.parent))
    fake_gitpro.answer(DO, '')
    path = put_config(machine, CONFIG + '\n[guard]\nchain_to = "~/data/llmpro/hooks"\non_mismatch = "warn"\n')
    cli.main(['setup', '-y', '--only', 'secrets', '--store', store])
    capsys.readouterr()
    assert cli.main(['uninstall', '-n', '--store', store]) == 1
    out = capsys.readouterr().out
    assert 'take chain_to out of the [guard] table of ~/.config/gitpro.toml and run gitpro setup -y --agent none' in out
    assert cli.main(['uninstall', '-y', '--store', store]) == 0
    cap = capsys.readouterr()
    assert path.read_text() == CONFIG + '\n[guard]\non_mismatch = "warn"\n'
    sh('sh', undo_of(cap.out))
    assert 'chain_to = "~/data/llmpro/hooks"' in path.read_text()


def test_uninstall_gives_back_the_config_as_it_was(guard, store, machine, fake_gitpro, capsys):
    guard()
    fake_gitpro.answer(DO, '')
    path = put_config(machine, CONFIG)
    cli.main(['setup', '-y', '--only', 'secrets', '--store', store])
    assert '[guard]' in path.read_text()
    guard(chain_to=str(guard.script.parent))
    cli.main(['uninstall', '-y', '--store', store])
    capsys.readouterr()
    assert path.read_text() == CONFIG


def test_uninstall_keeps_a_guard_table_with_a_comment(guard, store, machine, fake_gitpro, capsys):
    guard(chain_to=str(guard.script.parent))
    fake_gitpro.answer(DO, '')
    path = put_config(machine, CONFIG + '\n[guard]\n# mine\nchain_to = "~/data/llmpro/hooks"\n')
    cli.main(['setup', '-y', '--only', 'secrets', '--store', store])
    cli.main(['uninstall', '-y', '--store', store])
    capsys.readouterr()
    assert path.read_text() == CONFIG + '\n[guard]\n# mine\n'


def test_uninstall_leaves_a_chain_to_it_did_not_write(guard, store, machine, fake_gitpro, capsys):
    guard(chain_to=str(guard.script.parent))
    path = put_config(machine, CONFIG + '\n[guard]\nchain_to = "/elsewhere/../' + str(guard.script.parent) + '"\n')
    cli.main(['setup', '-y', '--only', 'secrets', '--store', store])
    capsys.readouterr()
    before = path.read_text()
    cli.main(['uninstall', '-y', '--store', store])
    cap = capsys.readouterr()
    assert path.read_text() == before
    assert 'llmpro did not write it; take it out by hand' in cap.err


def test_a_listed_value_and_basic_are_stopped(repo, env, store):
    listed = ''.join(['aB%c', 'D3eF', '%gHi', 'JkLm'])
    basic = base64.b64encode(''.join(['someone@mail.example', ':', 'Kq7vR2mZ', 'p9Lw4xTn']).encode()).decode()
    commit(repo, 'a.txt', f'["{listed}"]\n')
    assert push(repo, 'origin', 'main', env=env).returncode == 0  # not listed yet
    os.makedirs(os.path.join(store, '.agentspro'))
    with open(os.path.join(store, '.agentspro', 'blocklist.json'), 'w') as fh:
        json.dump({check.fp(listed): 'made up'}, fh)
    sha = commit(repo, 'b.txt', f'one\n["{listed}"]\n')
    r = push(repo, 'origin', 'main', env=env)
    assert r.returncode != 0 and f'{sha[:7]} b.txt:2 [listed]' in r.stderr and listed not in r.stderr
    sh('git', '-C', str(repo), 'reset', '-q', '--hard', 'HEAD~1')
    sha = commit(repo, 'c.txt', f'"Basic {basic}"\n')
    r = push(repo, 'origin', 'main', env=env)
    assert r.returncode != 0 and f'{sha[:7]} c.txt:1 [auth-header]' in r.stderr and basic not in r.stderr
