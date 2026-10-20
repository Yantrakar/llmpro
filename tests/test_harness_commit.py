"""Package 11: each chat records the store commit that was current when it started."""
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from llmpro import find, redact
from llmpro.importer import history
from llmpro.redact import Conversation, parse_frontmatter


def t(n):
    """A time that matches the committer time of commit n (see `commit`)."""
    return datetime.fromtimestamp(1_700_000_000 + n * 1000, timezone.utc)


class FakeTool:
    """A tool source with chats given as {id: started}."""

    def __init__(self, chats):
        self.chats = dict(chats)

    def source_files(self):
        return list(self.chats)

    def parse(self, src, titles):
        conv = Conversation('faketool', src, '/nowhere/' + src)
        conv.title = 'chat ' + src
        conv.project = '/work/demo'
        conv.started = self.chats[src]
        conv.updated = conv.started
        conv.msg('user', conv.started or t(0), 'hello there ' + src)
        return conv


@pytest.fixture
def real_git(tools_dir):
    os.symlink(shutil.which('git'), tools_dir / 'git')


def commit(store, n, name):
    with open(os.path.join(store, name), 'w') as fh:
        fh.write(name)
    when = f'@{1_700_000_000 + n * 1000} +0000'
    env = dict(os.environ, GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)
    subprocess.run(['git', '-C', store, 'add', '-A'], check=True, env=env)
    subprocess.run(['git', '-C', store, 'commit', '-q', '-m', name], check=True, env=env)
    out = subprocess.run(['git', '-C', store, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True)
    return out.stdout.strip()[:12]


def setup(store, monkeypatch, chats):
    """Three commits at times 1, 2, 3 and a fake tool with these chats; -> the three 12-char ids."""
    monkeypatch.setitem(history.TOOLS, 'faketool', FakeTool(chats))
    return commit(store, 1, 'a'), commit(store, 2, 'b'), commit(store, 3, 'c')


def outputs(folder):
    found = []
    for root, _, names in os.walk(folder):
        found += [os.path.join(root, n) for n in names]
    return found


def fields(store, chat_id, folder='history'):
    for path in outputs(os.path.join(store, folder)):
        f = parse_frontmatter(open(path, encoding='utf-8').read())[0]
        if f.get('id') == 'faketool:' + chat_id:
            return f, path
    raise AssertionError('no output for ' + chat_id)


def test_chat_between_second_and_third_gets_second(store, real_git, monkeypatch):
    a, b, c = setup(store, monkeypatch, {'x': t(2.5)})
    history.run(store, tools=['faketool'])
    assert fields(store, 'x')[0]['harness'] == b


def test_started_exactly_at_commit_time_counts(store, real_git, monkeypatch):
    a, b, c = setup(store, monkeypatch, {'x': t(3)})
    history.run(store, tools=['faketool'])
    assert fields(store, 'x')[0]['harness'] == c


def test_chat_before_first_commit_has_no_harness(store, real_git, monkeypatch):
    setup(store, monkeypatch, {'x': t(0)})
    history.run(store, tools=['faketool'])
    f, path = fields(store, 'x')
    assert 'harness' not in f
    assert '\nharness:' not in open(path).read()


def test_chat_without_start_time_has_no_harness(store, real_git, monkeypatch):
    setup(store, monkeypatch, {'x': None})
    history.run(store, tools=['faketool'])
    assert 'harness' not in fields(store, 'x')[0]


def test_harness_sits_right_after_updated(store, real_git, monkeypatch):
    setup(store, monkeypatch, {'x': t(2)})
    history.run(store, tools=['faketool'])
    keys = list(fields(store, 'x')[0])
    assert keys[keys.index('updated') + 1] == 'harness'


def test_existing_harness_kept_after_new_commit(store, real_git, monkeypatch):
    a, b, c = setup(store, monkeypatch, {'x': t(2)})
    history.run(store, tools=['faketool'])
    commit(store, 4, 'd')
    history.run(store, tools=['faketool'])
    assert fields(store, 'x')[0]['harness'] == b


def test_hand_set_harness_kept(store, real_git, monkeypatch):
    setup(store, monkeypatch, {'x': t(2)})
    history.run(store, tools=['faketool'])
    path = fields(store, 'x')[1]
    text = open(path).read()
    line = [ln for ln in text.splitlines() if ln.startswith('harness: ')][0]
    open(path, 'w').write(text.replace(line, 'harness: abc'))
    history.run(store, tools=['faketool'])
    assert fields(store, 'x')[0]['harness'] == 'abc'


def test_no_git_imports_without_harness(store, monkeypatch):
    setup(store, monkeypatch, {'x': t(2)})  # tools_dir is empty: tools.which finds no git
    history.run(store, tools=['faketool'])
    assert 'harness' not in fields(store, 'x')[0]


def test_one_git_call_for_several_chats(store, tools_dir, monkeypatch, tmp_path):
    log = tmp_path / 'git.log'
    wrapper = tools_dir / 'git'
    wrapper.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexec {shutil.which("git")} "$@"\n')
    wrapper.chmod(0o755)
    setup(store, monkeypatch, {'x': t(2), 'y': t(3), 'z': t(1)})
    history.run(store, tools=['faketool'])
    calls = log.read_text().splitlines()
    assert len(calls) == 1 and ' log ' in ' ' + calls[0] + ' '
    assert len(outputs(os.path.join(store, 'history'))) == 3


def test_local_chat_gets_harness(store, real_git, monkeypatch):
    monkeypatch.setattr(redact, 'is_local_only', lambda *paths: True)
    a, b, c = setup(store, monkeypatch, {'x': t(2)})
    history.run(store, tools=['faketool'])
    assert fields(store, 'x', folder='local/history')[0]['harness'] == b


def test_find_history_hit_has_harness(store, real_git, monkeypatch):
    a, b, c = setup(store, monkeypatch, {'x': t(2)})
    history.run(store, tools=['faketool'])
    hits = find.search(store, ['hello'])
    assert [h['harness'] for h in hits if h['kind'] == 'history'] == [b]
    json.dumps(hits)


def test_find_history_hit_without_harness_is_empty_string(store, monkeypatch):
    setup(store, monkeypatch, {'x': t(2)})
    history.run(store, tools=['faketool'])
    assert [h['harness'] for h in find.search(store, ['hello'])] == ['']


def test_memory_hits_have_no_harness(store):
    os.makedirs(os.path.join(store, 'memory'))
    with open(os.path.join(store, 'memory', 'm.md'), 'w') as fh:
        fh.write('---\nname: m\ndescription: hello\nscope: global\n---\nhello\n')
    hits = find.search(store, ['hello'], history=False)
    assert hits and all('harness' not in h for h in hits)
