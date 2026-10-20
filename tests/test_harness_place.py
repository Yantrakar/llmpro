"""setup places the harness: part folders, links into each tool, scripts on PATH, and uninstall's copies."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from llmpro import cli, harness, setup, store as cfg

PARTS = ['instructions', 'memory', 'skills', 'subagents', 'hooks', 'guardrails', 'scripts']


def run(argv, capsys):
    code = cli.main(argv)
    c = capsys.readouterr()
    return code, c.out, c.err


def line(out, part):
    found = [l.strip() for l in out.splitlines() if l.strip().startswith(part + ':')]
    assert len(found) == 1, out
    return found[0]


def undo_path(out):
    lines = [l for l in out.splitlines() if l.startswith('To undo: sh ')]
    assert len(lines) == 1, out
    return lines[0][len('To undo: sh '):]


def tree(path):
    """Every path under a folder: relative name -> file text, None for a folder, or ('link', target)."""
    got = {}
    for d, dirs, files in os.walk(path):
        if '.git' in dirs:
            dirs.remove('.git')
        for name in dirs + files:
            p = os.path.join(d, name)
            rel = os.path.relpath(p, path)
            if os.path.islink(p):
                got[rel] = ('link', os.readlink(p))
            elif os.path.isdir(p):
                got[rel] = None
            else:
                got[rel] = open(p).read()
    return got


@pytest.fixture(autouse=True)
def quiet_machine(monkeypatch, onpath):
    monkeypatch.setattr(setup, 'running_tools', lambda: [])


def put(path, text='x\n', mode=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode:
        path.chmod(mode)


def test_template_and_bare_store_list_all_seven(tmp_path, store):
    (tmp_path / 'config.toml').write_text(open(os.path.join(setup.TEMPLATES, 'config.toml'), encoding='utf-8').read())
    cfg.configure(str(tmp_path))
    assert [p for p, _ in cfg.harness_parts()] == PARTS
    cfg.configure(store)
    assert [p for p, _ in cfg.harness_parts()] == PARTS
    assert dict(cfg.harness_parts())['scripts'] == 'scripts/'


def test_claude_only_machine_gets_folders_and_a_link(home, store, capsys):
    (home / '.claude').mkdir()
    code, out, _ = run(['setup', '-n'], capsys)
    assert code == 1
    sub = line(out, 'subagents')
    assert 'claude-code missing' in sub and 'codex not here' in sub
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 0, err
    for part in ('agents', 'hooks', 'guardrails', 'scripts'):
        assert os.path.isfile(os.path.join(store, part, 'README.md')), part
    assert 'created' in err and 'linked' in err
    link = home / '.claude/agents'
    assert os.path.islink(link) and os.path.realpath(link) == os.path.realpath(os.path.join(store, 'agents/claude-code'))
    assert not os.path.exists(home / '.codex')
    assert run(['setup', '-n'], capsys)[0] == 0


def test_readme_says_where_each_tool_gets_it(home, store, capsys):
    (home / '.claude').mkdir()
    run(['setup', '-y'], capsys)
    text = open(os.path.join(store, 'agents/README.md')).read()
    assert 'agents/<tool>/' in text and '~/.claude/agents' in text and '~/.codex/agents' in text
    assert '—' not in text
    assert 'guardrails/claude-code/' in open(os.path.join(store, 'guardrails/README.md')).read()


def test_real_files_move_into_the_store_and_undo_puts_them_back(home, store, capsys):
    put(home / '.claude/agents/a.md', 'agent a\n')
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 0, err
    link = home / '.claude/agents'
    assert os.path.islink(link)
    assert open(os.path.join(store, 'agents/claude-code/a.md')).read() == 'agent a\n'
    assert (link / 'a.md').read_text() == 'agent a\n'
    subprocess.run(['sh', undo_path(out)], check=True)
    assert not os.path.islink(link) and os.path.isdir(link)
    assert (link / 'a.md').read_text() == 'agent a\n'
    assert not os.path.exists(os.path.join(store, 'agents/claude-code/a.md'))


def test_todo_says_files_move_first(home, store, capsys):
    put(home / '.claude/agents/a.md')
    put(home / '.claude/agents/b.md')
    err = run(['setup', '-y'], capsys)[2]
    assert 'link ~/.claude/agents -> ~/.agents/agents/claude-code (moves its 2 files into the store first)' in err
    assert 'create agents/ with a README.md' in err


def test_name_on_both_sides_is_a_clash(home, store, capsys):
    put(home / '.claude/agents/a.md', 'mine\n')
    put(Path(store) / 'agents/claude-code/a.md', 'store\n')
    code, out, _ = run(['setup', '-n'], capsys)
    assert 'claude-code clash' in line(out, 'subagents')
    assert 'merge ~/.claude/agents/a.md into' in out and 'agents/claude-code/a.md by hand, then run llmpro setup' in out
    assert harness.state(store, 'subagents', 'claude-code')[0] == 'clash'
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 0
    assert (home / '.claude/agents/a.md').read_text() == 'mine\n'
    assert not os.path.islink(home / '.claude/agents')
    assert open(os.path.join(store, 'agents/claude-code/a.md')).read() == 'store\n'
    assert run(['setup', '-n'], capsys)[0] == 0


def test_link_elsewhere_is_left_alone(home, store, tmp_path, capsys):
    (home / '.claude').mkdir()
    other = tmp_path / 'other-agents'
    other.mkdir()
    os.symlink(other, home / '.claude/agents')
    code, out, _ = run(['setup', '-n'], capsys)
    assert 'claude-code linked elsewhere' in line(out, 'subagents')
    assert 'llmpro leaves it; remove the link to let setup place it' in out and str(other) in out
    assert run(['setup', '-y'], capsys)[0] == 0
    assert os.readlink(home / '.claude/agents') == str(other)
    assert run(['setup', '-n'], capsys)[0] == 0


def test_codex_hooks_file(home, store, capsys):
    (home / '.codex').mkdir()
    out = run(['setup', '-n'], capsys)[1]
    assert 'codex nothing yet' in line(out, 'hooks')
    assert harness.state(store, 'hooks', 'codex')[0] == 'nothing yet'
    put(Path(store) / 'hooks/codex/hooks.json', '{}\n')
    assert 'codex missing' in line(run(['setup', '-n'], capsys)[1], 'hooks')
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 0, err
    link = home / '.codex/hooks.json'
    assert os.path.islink(link) and link.read_text() == '{}\n'
    assert 'codex placed' in line(run(['setup', '-n'], capsys)[1], 'hooks')


def test_codex_own_hooks_file_moves_into_the_store(home, store, capsys):
    put(home / '.codex/hooks.json', '{"a": 1}\n')
    out = run(['setup', '-y'], capsys)[1]
    assert os.path.islink(home / '.codex/hooks.json')
    assert open(os.path.join(store, 'hooks/codex/hooks.json')).read() == '{"a": 1}\n'
    subprocess.run(['sh', undo_path(out)], check=True)
    assert (home / '.codex/hooks.json').read_text() == '{"a": 1}\n'
    assert not os.path.islink(home / '.codex/hooks.json')


def test_skills_for_direct_readers_in_the_home_store(home, store, capsys):
    (home / '.codex').mkdir()
    sk = line(run(['setup', '-n'], capsys)[1], 'skills')
    assert 'codex placed' in sk and 'copilot-cli placed' in sk and 'opencode placed' in sk
    run(['setup', '-y'], capsys)
    assert not os.path.lexists(home / '.codex/skills')
    assert not os.path.lexists(home / '.config/opencode/skills')


def test_skills_elsewhere_codex_is_unsupported(home, tmp_path, capsys):
    other = tmp_path / 'other'
    other.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(other)], check=True)
    (other / 'config.toml').write_text('[store]\nformat = 1\nredact_version = 1\n')
    (home / '.codex').mkdir()
    (home / '.config/opencode').mkdir(parents=True)
    out = run(['--store', str(other), 'setup', '-n'], capsys)[1]
    assert 'Codex reads only ~/.agents/skills' in line(out, 'skills')
    assert 'opencode missing' in line(out, 'skills')
    cfg.load_harness(str(other))
    assert harness.state(str(other), 'skills', 'codex') == ('unsupported', 'Codex reads only ~/.agents/skills')


def test_guardrails_and_claude_hooks_are_not_placed_yet(home, store, capsys):
    (home / '.claude').mkdir()
    (home / '.codex').mkdir()
    out = run(['setup', '-n'], capsys)[1]
    assert 'not placed yet' in line(out, 'hooks')
    assert 'put guardrails in hooks/codex/' in line(out, 'guardrails')


def test_scripts(home, store, capsys):
    s = Path(store) / 'scripts'
    put(s / 'hello', '#!/bin/sh\necho hi\n', 0o755)
    put(s / 'plain', 'x\n', 0o644)
    put(s / 'taken', '#!/bin/sh\n', 0o755)
    put(home / '.local/bin/taken', 'mine\n', 0o755)
    code, out, _ = run(['setup', '-n'], capsys)
    assert code == 1
    got = line(out, 'scripts')
    assert '1 missing (hello)' in got and '1 not executable (plain)' in got and '1 clash (taken)' in got
    assert 'chmod +x' in out
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 0, err
    link = home / '.local/bin/hello'
    assert os.path.islink(link) and os.path.realpath(link) == os.path.realpath(s / 'hello')
    assert (home / '.local/bin/taken').read_text() == 'mine\n' and not os.path.islink(home / '.local/bin/taken')
    assert not os.path.lexists(home / '.local/bin/plain')
    assert '1 placed' in line(run(['setup', '-n'], capsys)[1], 'scripts')
    subprocess.run(['sh', undo_path(out)], check=True)
    assert not os.path.lexists(link)
    cfg.load_harness(store)
    assert harness.script_states(store)[0][0] == 'hello'


def test_no_scripts_says_none_yet(home, store, capsys):
    assert line(run(['setup', '-n'], capsys)[1], 'scripts').endswith('none yet')


def test_uninstall_leaves_real_copies(home, store, capsys):
    put(home / '.claude/agents/a.md', 'agent a\n')
    put(Path(store) / 'scripts/hello', '#!/bin/sh\n', 0o755)
    assert run(['setup', '-y'], capsys)[0] == 0
    assert os.path.islink(home / '.claude/agents') and os.path.islink(home / '.local/bin/hello')
    before = tree(store)
    code, out, _ = run(['uninstall', '-n'], capsys)
    assert 'replace the link ~/.claude/agents with a copy of ~/.agents/agents/claude-code' in out
    code, out, err = run(['uninstall', '-y'], capsys)
    assert code == 0, err
    for p in (home / '.claude/agents', home / '.claude/skills'):
        assert os.path.isdir(p) and not os.path.islink(p)
    assert (home / '.claude/agents/a.md').read_text() == 'agent a\n'
    assert (home / '.local/bin/hello').read_text() == '#!/bin/sh\n'
    assert not os.path.islink(home / '.local/bin/hello')
    assert tree(store) == before
    assert run(['uninstall', '-n'], capsys)[1].strip() == 'nothing of llmpro is set up on this machine'
    subprocess.run(['sh', undo_path(out)], check=True)
    assert os.path.islink(home / '.claude/agents') and os.path.islink(home / '.local/bin/hello')
    assert os.path.realpath(home / '.claude/agents') == os.path.realpath(os.path.join(store, 'agents/claude-code'))


def test_json_item_flips_to_present(home, store, capsys):
    (home / '.claude').mkdir()
    got = json.loads(run(['setup', '-n', '--json'], capsys)[1])
    assert 'harness' in got['missing']
    item = [i for i in got['items'] if i['name'] == 'harness'][0]
    assert item['present'] is False and set(item) == {'name', 'present', 'details'}
    assert run(['setup', '-y'], capsys)[0] == 0
    got = json.loads(run(['setup', '-n', '--json'], capsys)[1])
    assert got['missing'] == []
    assert [i for i in got['items'] if i['name'] == 'harness'][0]['present'] is True
