"""[privacy] local_identities: chats of repos that gitpro places under a listed identity stay in local/.
Real `git init` folders under the temporary HOME, the fake gitpro answering `why`, a fixture store."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from llmpro import cli, store as cfg


def git(d, *args):
    return subprocess.run(['git', '-C', str(d), *args], capture_output=True, text=True, check=True).stdout


def repo(path):
    path.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    return path


def chat(home, folder, sid):
    """A Claude Code session whose working folder is `folder` (the folder need not exist)."""
    d = home / '.claude' / 'projects' / ('p-' + sid)
    d.mkdir(parents=True, exist_ok=True)
    line = {'type': 'user', 'sessionId': sid, 'uuid': 'u-' + sid, 'cwd': str(folder), 'timestamp': '2026-01-01T00:00:00Z',
            'message': {'role': 'user', 'content': 'hello ' + sid}}
    (d / (sid + '.jsonl')).write_text(json.dumps(line) + '\n')


def why(fg, folder, ident, exit=0):
    fg.answer(['why', str(folder), '--json'], {'ok': True, 'command': 'why', 'expected': {'id': ident}}, exit=exit)


def set_identities(store, *ids):
    path = os.path.join(store, 'config.toml')
    text = open(path).read().replace('local_only = []', 'local_only = []\nlocal_identities = ' + json.dumps(list(ids)))
    open(path, 'w').write(text)


def sync(store, capsys, *extra):
    code = cli.main(['sync', '--local', '-q', *extra, '--store', store])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def files(store, where):
    out = []
    for dirpath, _, names in os.walk(os.path.join(store, where)):
        out += [os.path.join(dirpath, n) for n in names if n.endswith('.md')]
    return sorted(out)


def shared(store):
    return files(store, 'history')


def local(store):
    return files(store, 'local')


def asked(fg):
    return [c for c in fg.calls() if c[:1] == ['why']]


@pytest.fixture(autouse=True)
def clean(onpath, home, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(home / 'state'))
    yield
    cfg.LOCAL_IDENTITIES[:] = []
    cfg.NOW_LOCAL[:] = []
    sys.modules.pop('llmpro.privacy', None)


@pytest.fixture
def fg(fake_gitpro):
    return fake_gitpro


def test_empty_list_does_not_import_privacy(home, store, capsys):
    work = repo(home / 'code' / 'work-app')
    chat(home, work, 's1')
    sys.modules.pop('llmpro.privacy', None)
    assert sync(store, capsys)[0] == 0
    assert 'llmpro.privacy' not in sys.modules
    assert len(shared(store)) == 1 and local(store) == []


def test_identity_decides_the_place(home, store, fg, capsys):
    work, hobby = repo(home / 'code' / 'work-app'), repo(home / 'code' / 'hobby')
    why(fg, work, 'work')
    why(fg, hobby, 'personal')
    chat(home, work, 'w1')
    chat(home, hobby, 'h1')
    set_identities(store, 'work')
    code, out, err = sync(store, capsys)
    assert code == 0, err
    (loc,) = local(store)
    assert os.path.relpath(loc, store).startswith(os.path.join('local', 'history') + os.sep)
    (sh,) = shared(store)
    assert 'hello h1' in open(sh).read() and 'hello w1' in open(loc).read()
    assert os.path.exists(os.path.join(store, 'local', 'manifest.jsonl'))


def test_default_identity_is_null(home, store, fg, capsys):
    work = repo(home / 'code' / 'work-app')
    why(fg, work, None, exit=1)  # exit 1 still carries the full answer
    chat(home, work, 'w1')
    set_identities(store, 'default')
    assert sync(store, capsys)[0] == 0
    assert len(local(store)) == 1 and shared(store) == []


def test_not_asked_for_plain_folders_home_and_store(home, store, fg, capsys):
    plain = home / 'code' / 'plain'
    plain.mkdir(parents=True)
    chat(home, plain, 'a1')
    subprocess.run(['git', 'init', '-q', str(home)], check=True)  # the home folder is a repo
    chat(home, home, 'b1')
    chat(home, store, 'c1')
    chat(home, os.path.join(store, 'memory'), 'd1')
    set_identities(store, 'work')
    code, out, err = sync(store, capsys)
    assert code == 0, err
    assert len(shared(store)) == 4 and local(store) == []
    assert asked(fg) == []


def test_gitpro_error_keeps_chat_local_and_warns_once(home, store, fg, capsys):
    work = repo(home / 'code' / 'work-app')
    fg.answer(['why', str(work), '--json'], {'ok': False, 'command': 'why', 'error': 'boom'}, exit=2)
    chat(home, work, 'w1')
    chat(home, work, 'w2')
    set_identities(store, 'work')
    code, out, err = sync(store, capsys)
    assert code == 0, err
    assert len(local(store)) == 2 and shared(store) == []
    assert err.count('llmpro: could not place ~/code/work-app with gitpro (') == 1
    assert '); its chats stay local' in err
    assert len(asked(fg)) == 1


def test_answer_without_expected_is_local(home, store, fg, capsys):
    work = repo(home / 'code' / 'work-app')
    fg.answer(['why', str(work), '--json'], {'ok': True, 'command': 'why'})
    chat(home, work, 'w1')
    set_identities(store, 'work')
    code, out, err = sync(store, capsys)
    assert len(local(store)) == 1 and 'could not place ~/code/work-app' in err


def test_deleted_folder_uses_the_cache_or_stays_local(home, store, fg, capsys):
    work = repo(home / 'code' / 'work-app')
    why(fg, work, 'personal')
    chat(home, work, 'w1')
    set_identities(store, 'work')
    assert sync(store, capsys)[0] == 0  # w1 is shared; the answer is cached
    assert len(shared(store)) == 1
    shutil.rmtree(work)
    chat(home, work, 'w2')  # the folder is gone; the cached answer says personal
    code, out, err = sync(store, capsys)
    assert code == 0 and len(shared(store)) == 2 and 'could not place' not in err
    chat(home, home / 'code' / 'never-seen', 'n1')  # gone and never asked
    code, out, err = sync(store, capsys)
    assert len(local(store)) == 1
    assert err.count('could not place ~/code/never-seen with gitpro (folder is gone); its chats stay local') == 1


@pytest.mark.parametrize('version', [None, '0.3.4'])
def test_gitpro_missing_or_old_stops_sync(home, store, fake_gitpro, capsys, version):
    if version:
        fake_gitpro.version(version, capabilities=())
    else:
        os.remove(fake_gitpro.path)
    chat(home, repo(home / 'code' / 'work-app'), 'w1')
    set_identities(store, 'work')
    code, out, err = sync(store, capsys)
    assert code == 1
    assert '[privacy] local_identities needs gitpro 0.4 or later (' in err
    assert 'Or take local_identities out of config.toml.' in err
    assert shared(store) == [] and local(store) == []
    code = cli.main(['sync', '-n', '--json', '--store', store])
    got = json.loads(capsys.readouterr().out)
    assert got['ok'] is False and code == 1
    assert 'local_identities needs gitpro 0.4' in json.dumps(got)


def test_missing_why_capability_stops_sync(home, store, fake_gitpro, capsys):
    fake_gitpro.version('0.4.0', capabilities=('setup',))
    set_identities(store, 'work')
    code, out, err = sync(store, capsys)
    assert code == 1 and 'local_identities needs gitpro 0.4' in err


def test_empty_list_needs_no_gitpro(home, store, capsys):
    chat(home, repo(home / 'code' / 'work-app'), 'w1')
    assert sync(store, capsys)[0] == 0


def test_chat_already_shared_stays_and_is_reported(home, store, fg, capsys):
    work = repo(home / 'code' / 'work-app')
    why(fg, work, 'work')
    chat(home, work, 'w1')
    assert sync(store, capsys)[0] == 0  # identities not set yet: shared
    (before,) = shared(store)
    set_identities(store, 'work')
    code, out, err = sync(store, capsys)
    assert code == 0, err
    assert shared(store) == [before] and local(store) == []
    rel = os.path.relpath(before, store)
    assert f'already shared, now local by identity: {rel}; move it by hand (see FORMAT.md)' in err
    assert '1 chat' in err
    # the dry run lists the same chat
    cli.main(['sync', '-n', '--json', '--store', store])
    got = json.loads(capsys.readouterr().out)
    assert got['now_local'] == [rel]
    cli.main(['sync', '-n', '--store', store])
    assert f'already shared, now local by identity: {rel}; move it by hand' in capsys.readouterr().out
    # a new chat in the same repo goes to local/
    chat(home, work, 'w2')
    assert sync(store, capsys)[0] == 0
    assert len(local(store)) == 1 and shared(store) == [before]


def test_same_repo_is_asked_once(home, store, fg, capsys):
    work = repo(home / 'code' / 'work-app')
    (work / 'sub').mkdir()
    why(fg, work, 'work')
    for sid, folder in (('a1', work), ('a2', work), ('a3', work / 'sub')):
        chat(home, folder, sid)
    set_identities(store, 'work')
    assert sync(store, capsys)[0] == 0
    assert len(local(store)) == 3
    assert len(asked(fg)) == 1


def test_cache_is_private_and_outside_the_store(home, store, fg, capsys):
    work = repo(home / 'code' / 'work-app')
    why(fg, work, 'work')
    chat(home, work, 'w1')
    set_identities(store, 'work')
    assert sync(store, capsys)[0] == 0
    path = home / 'state' / 'llmpro' / 'privacy.json'
    assert path.exists() and (path.stat().st_mode & 0o777) == 0o600
    data = json.loads(path.read_text())
    assert data[str(work)]['id'] == 'work' and data[str(work)]['checked'].startswith('20')
    assert 'privacy.json' not in git(store, 'ls-files') + git(store, 'status', '--porcelain', '--ignored')
    path.unlink()  # a dry run asks gitpro but does not write it
    cli.main(['sync', '-n', '--store', store])
    assert not path.exists()


def test_config_must_be_a_list(store):
    path = os.path.join(store, 'config.toml')
    text = open(path).read().replace('local_only = []', 'local_only = []\nlocal_identities = "work"')
    open(path, 'w').write(text)
    with pytest.raises(SystemExit):
        cfg.configure(store)
