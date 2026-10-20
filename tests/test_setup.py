"""setup and uninstall in a temporary HOME with fake tool folders (nothing real is read or written)."""
import hashlib
import json
import os
import stat
import subprocess
import sys

import pytest

from llmpro import cli, setup


def snapshot(home, skip=('.agents', 'b-store', 'state')):
    """Every path under home outside the store and the state folder: kind, link target or content hash."""
    snap = {}
    for d, dirs, files in os.walk(home):
        dirs[:] = [x for x in dirs if not (d == str(home) and x in skip)]
        for name in dirs + files:
            p = os.path.join(d, name)
            rel = os.path.relpath(p, home)
            if os.path.islink(p):
                snap[rel] = ('link', os.readlink(p))
            elif os.path.isdir(p):
                snap[rel] = ('dir',)
            else:
                snap[rel] = ('file', hashlib.sha256(open(p, 'rb').read()).hexdigest())
    return snap


def undo_path(out):
    lines = [l for l in out.splitlines() if l.startswith('To undo: sh ')]
    assert len(lines) == 1, out
    return lines[0][len('To undo: sh '):]


def run(argv, capsys):
    code = cli.main(argv)
    c = capsys.readouterr()
    return code, c.out, c.err


def test_dry_run_on_bare_machine_lists_everything_missing_and_writes_nothing(machine, capsys):
    before = snapshot(machine)
    code, out, _ = run(['setup', '-n'], capsys)
    assert code == 1
    assert 'store' in out and 'missing' in out
    assert snapshot(machine) == before
    assert not (machine / 'state').exists() and not (machine / '.agents').exists()


def test_no_terminal_and_no_yes_refuses(machine, capsys, monkeypatch):
    monkeypatch.setattr(setup, 'interactive', lambda: False)
    before = snapshot(machine)
    code, out, err = run(['setup'], capsys)
    assert code == 2
    assert '-y' in err and '-n' in err
    assert snapshot(machine) == before and not (machine / '.agents').exists()


def test_uninstall_without_terminal_refuses_too(machine, capsys, monkeypatch):
    assert run(['setup', '-y'], capsys)[0] == 0
    monkeypatch.setattr(setup, 'interactive', lambda: False)
    before = snapshot(machine)
    code, out, err = run(['uninstall'], capsys)
    assert code == 2 and '-y' in err and '-n' in err
    assert snapshot(machine) == before


def test_setup_yes_does_everything(machine, capsys, onpath):
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 0, err
    store = machine / '.agents'
    for name in ('AGENTS.md', 'FORMAT.md', 'config.toml', '.gitignore', '.gitattributes'):
        assert (store / name).is_file()
    assert (store / '.git').is_dir()
    for name in ('pre-commit', 'pre-merge-commit', 'pre-push'):
        hook = store / '.git' / 'hooks' / name
        assert hook.read_text() == f'#!/bin/sh\nexec llmpro hook {name} "$@"\n'
        assert os.access(hook, os.X_OK)
    assert subprocess.run(['git', '-C', str(store), 'config', 'merge.ours.driver'], capture_output=True, text=True).stdout.strip() == 'true'
    link = machine / '.claude/projects'
    assert link.is_symlink() and os.readlink(link) == str(store / 'sessions/claude-code/projects')
    assert (store / 'sessions/claude-code/projects/p1/s1.jsonl').read_text() == '{"a": 1}\n'
    assert (machine / '.local/share/opencode').is_symlink()
    link = machine / '.local/state/opencode'
    assert link.is_symlink() and os.readlink(link) == str(store / 'sessions/opencode/state')
    assert (store / 'sessions/opencode/state/prompt-history.jsonl').read_text() == '{}\n'
    assert not (machine / '.config/Code').exists()  # not installed: not created
    # pointers: Claude Code with an @ line, the others with a sentence, existing text kept
    assert (machine / '.claude/CLAUDE.md').read_text() == '# mine\nkeep this\n@~/.agents/AGENTS.md\n'
    assert (machine / '.codex/AGENTS.md').read_text() == 'codex rules\nRead ~/.agents/AGENTS.md at the start of every session.\n'
    assert (machine / '.config/opencode/AGENTS.md').is_file()
    assert not (machine / '.copilot/copilot-instructions.md').read_text().startswith('@')
    # credentials and settings are untouched
    assert (machine / '.codex/auth.json').read_text() == '{"fake": "not a real credential"}\n'
    assert (machine / '.claude/settings.json').read_text() == '{"fake": true}\n'
    # the undo script: where it is, its mode, the state folder's mode
    path = undo_path(out)
    assert path.startswith(str(machine / 'state/llmpro/'))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(machine / 'state/llmpro').st_mode) == 0o700


def test_second_setup_shows_nothing_missing(machine, capsys, onpath):
    assert run(['setup', '-y'], capsys)[0] == 0
    scripts = os.listdir(machine / 'state/llmpro')
    code, out, _ = run(['setup', '-n'], capsys)
    assert code == 0 and not [l for l in out.splitlines() if not l.startswith(' ') and l.endswith('missing')]  # item states only; harness details may say missing
    before = snapshot(machine)
    code, out, _ = run(['setup', '-y'], capsys)
    assert code == 0 and 'in place' in out and 'To undo' not in out
    assert snapshot(machine) == before
    assert os.listdir(machine / 'state/llmpro') == scripts


def test_dry_run_json(machine, capsys, onpath):
    code, out, _ = run(['setup', '-n', '--json'], capsys)
    got = json.loads(out)
    assert code == 1 and 'store' in got['missing']
    assert run(['setup', '-y'], capsys)[0] == 0
    code, out, _ = run(['setup', '-n', '--json'], capsys)
    assert code == 0 and json.loads(out)['missing'] == []


def test_undo_script_restores_the_original_state(machine, capsys, onpath):
    before = snapshot(machine)
    code, out, _ = run(['setup', '-y'], capsys)
    assert code == 0
    assert snapshot(machine) != before
    subprocess.run(['sh', undo_path(out)], check=True)
    assert snapshot(machine) == before  # folders back, symlinks gone, instruction files byte for byte
    assert (machine / '.agents').is_dir()  # the store is kept


def test_undo_restores_existing_hooks_and_hookspath(machine, capsys, onpath, store):
    sh = lambda *a: subprocess.run(['git', '-C', store, *a], capture_output=True, text=True, check=True).stdout.strip()
    sh('config', 'core.hooksPath', 'tools/hooks')
    hook = os.path.join(store, '.git', 'hooks', 'pre-commit')
    os.makedirs(os.path.dirname(hook), exist_ok=True)
    open(hook, 'w').write('#!/bin/sh\necho "mine"\n')
    os.chmod(hook, 0o750)
    code, out, err = run(['setup', '-y', '--store', store], capsys)
    assert code == 0, err
    said = out + err
    assert 'unset core.hooksPath' in said and 'set in the store: hook pre-commit' in said
    assert 'core.hooksPath' not in said.split('set in the store:')[1].split('\n')[0]  # never listed as a thing to set
    assert subprocess.run(['git', '-C', store, 'config', 'core.hooksPath'], capture_output=True).returncode != 0
    assert 'llmpro hook' in open(hook).read()
    subprocess.run(['sh', undo_path(out)], check=True)
    assert sh('config', 'core.hooksPath') == 'tools/hooks'
    assert open(hook).read() == '#!/bin/sh\necho "mine"\n'
    assert stat.S_IMODE(os.stat(hook).st_mode) == 0o750
    assert not os.path.exists(os.path.join(store, '.git', 'hooks', 'pre-push'))
    assert subprocess.run(['git', '-C', store, 'config', 'merge.manifest.driver'], capture_output=True).returncode != 0


def test_refuses_while_a_tool_runs_and_names_it(machine, capsys, monkeypatch, onpath):
    monkeypatch.setattr(setup, 'running_tools', lambda: ['claude', 'code'])
    before = snapshot(machine)
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 2
    assert ('claude and code are running, and setup would move their session folders while they write to them. '
            'Nothing was changed. Close them, then run llmpro setup again') in err
    assert snapshot(machine) == before and not (machine / '.agents').exists()


def test_session_folder_with_existing_target_is_left_alone(machine, capsys, onpath, store):
    os.makedirs(os.path.join(store, 'sessions/codex/sessions'))
    code, out, err = run(['setup', '-y', '--store', store], capsys)
    assert code == 0
    assert not (machine / '.codex/sessions').is_symlink()  # skipped, to be sorted out by hand
    assert (machine / '.claude/projects').is_symlink()
    assert run(['setup', '-n', '--store', store], capsys)[0] == 0


def test_session_folder_is_not_moved_into_another_tools_linked_folder(machine, capsys, onpath, store):
    # an older layout: copilot cli's folder links to sessions/copilot, where VS Code's would go
    os.makedirs(os.path.join(store, 'sessions/copilot'))
    os.rename(machine / '.copilot/session-state', os.path.join(store, 'sessions/copilot/a'))
    os.rmdir(machine / '.copilot')
    os.makedirs(machine / '.copilot')
    os.symlink(os.path.join(store, 'sessions/copilot'), machine / '.copilot/session-state')
    (machine / '.config/Code/User/workspaceStorage/w1').mkdir(parents=True)
    code, out, err = run(['setup', '-n', '--store', store], capsys)
    assert 'would be inside the folder ~/.copilot/session-state links to' in out
    code, out, err = run(['setup', '-y', '--store', store], capsys)
    assert code == 0
    assert not (machine / '.config/Code/User/workspaceStorage').is_symlink()
    assert (machine / '.config/Code/User/workspaceStorage/w1').is_dir()
    assert sorted(os.listdir(os.path.join(store, 'sessions/copilot'))) == ['a']
    assert (machine / '.claude/projects').is_symlink()


def test_refuses_to_move_logs_into_a_store_that_does_not_ignore_them(machine, capsys, onpath, store):
    open(os.path.join(store, '.gitignore'), 'w').write('*.pyc\n')
    before = snapshot(machine)
    code, out, err = run(['setup', '-y', '--store', store], capsys)
    assert code == 2
    assert ('.gitignore does not ignore /sessions/, so the raw logs setup would move there could be committed; '
            'llmpro does not move them. Add the line /sessions/ to ') in err
    assert '.gitignore, then run llmpro setup' in err
    assert snapshot(machine) == before


def test_prompt_no_changes_nothing_yes_applies(machine, capsys, monkeypatch, onpath):
    monkeypatch.setattr(setup, 'interactive', lambda: True)
    before = snapshot(machine)
    monkeypatch.setattr('builtins.input', lambda prompt='': 'n')
    code, out, err = run(['setup'], capsys)
    assert code == 1 and 'Nothing changed' in err and 'would' in err
    assert snapshot(machine) == before and not (machine / '.agents').exists()
    monkeypatch.setattr('builtins.input', lambda prompt='': 'y')
    assert run(['setup'], capsys)[0] == 0
    assert (machine / '.claude/projects').is_symlink()


def test_not_a_store_is_an_error(machine, capsys):
    (machine / '.agents').mkdir()
    (machine / '.agents/notes.txt').write_text('mine\n')
    code, out, err = run(['setup', '-y'], capsys)
    assert code == 2 and 'not a store' in err
    assert (machine / '.agents/notes.txt').read_text() == 'mine\n'


def test_hub_clones_the_store(machine, capsys, onpath, tmp_path):
    # a store made elsewhere, pushed to a local bare repo that stands in for the hub
    other = tmp_path / 'other'
    other.mkdir()
    for name in ('config.toml', '.gitignore', 'AGENTS.md'):
        (other / name).write_text(open(os.path.join(os.path.dirname(setup.__file__), 'templates', name)).read())
    git = lambda d, *a: subprocess.run(['git', '-C', str(d), *a], check=True, capture_output=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(other)], check=True)
    git(other, '-c', 'user.name=t', '-c', 'user.email=t@example.com', 'add', '-A')
    git(other, '-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '-m', 'first')
    bare = tmp_path / 'hub.git'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(bare)], check=True)
    git(other, 'push', '-q', str(bare), 'main')
    code, out, err = run(['setup', '-y', '--hub', str(bare)], capsys)
    assert code == 0, err
    store = machine / '.agents'
    assert (store / 'config.toml').is_file()
    assert subprocess.run(['git', '-C', str(store), 'remote', 'get-url', 'hub'], capture_output=True, text=True).stdout.strip() == str(bare)
    assert run(['setup', '-n'], capsys)[0] == 0


# ---------------------------------------------------------------- uninstall

def test_uninstall_reverses_setup_and_keeps_the_store(machine, capsys, onpath):
    before = snapshot(machine)
    assert run(['setup', '-y'], capsys)[0] == 0
    store = machine / '.agents'
    (store / 'history-note.md').write_text('keep me\n')
    code, out, err = run(['uninstall', '-n'], capsys)
    assert code == 1 and 'back to' in out
    assert (machine / '.claude/projects').is_symlink()  # -n changed nothing
    code, out, err = run(['uninstall', '-y'], capsys)
    assert code == 0, err
    assert (machine / '.claude/CLAUDE.md').read_text() == '# mine\nkeep this\n'  # setup added the final newline
    now = snapshot(machine)
    now.pop('.claude/CLAUDE.md'), before.pop('.claude/CLAUDE.md')
    from llmpro.harness import PLACES
    places = [place for _, _, place, _ in PLACES if place]  # uninstall leaves a real copy of each harness link
    now = {k: v for k, v in now.items() if not any(k == p or k.startswith(p + '/') for p in places)}
    assert now == before
    assert (store / 'history-note.md').read_text() == 'keep me\n'
    assert (store / 'sessions').is_dir()  # the store is never deleted
    for name in ('pre-commit', 'pre-merge-commit', 'pre-push'):
        assert not (store / '.git' / 'hooks' / name).exists()
    assert subprocess.run(['git', '-C', str(store), 'config', 'merge.manifest.driver'], capture_output=True).returncode != 0
    assert run(['uninstall', '-n'], capsys)[0] == 0


def test_uninstall_undo_script_sets_everything_up_again(machine, capsys, onpath):
    assert run(['setup', '-y'], capsys)[0] == 0
    after_setup = snapshot(machine)
    code, out, _ = run(['uninstall', '-y'], capsys)
    assert code == 0
    assert stat.S_IMODE(os.stat(undo_path(out)).st_mode) == 0o700
    subprocess.run(['sh', undo_path(out)], check=True)
    assert snapshot(machine) == after_setup
    assert run(['setup', '-n'], capsys)[0] == 0


def test_uninstall_refuses_while_a_tool_runs(machine, capsys, monkeypatch, onpath):
    assert run(['setup', '-y'], capsys)[0] == 0
    monkeypatch.setattr(setup, 'running_tools', lambda: ['codex'])
    before = snapshot(machine)
    code, out, err = run(['uninstall', '-y'], capsys)
    assert code == 2
    assert ('codex is running, and uninstall would move its session folders while it writes to them. '
            'Nothing was changed. Close it, then run llmpro uninstall again') in err
    assert snapshot(machine) == before


def test_a_folder_that_is_not_a_store(machine, capsys, onpath, tmp_path):
    other = tmp_path / 'notes'
    other.mkdir()
    (other / 'todo.txt').write_text('x\n')
    want = (f'{other} exists and is not empty, but has no .git and config.toml, so it is not a store; llmpro changed '
            f'nothing there. Move it (mv {other} {other}.old) or choose another folder with --store PATH, '
            'then run llmpro setup')
    code, out, _ = run(['setup', '-n', '--store', str(other)], capsys)
    assert want in out
    code, out, err = run(['setup', '-y', '--store', str(other)], capsys)
    assert code == 2 and want in err
    assert os.listdir(other) == ['todo.txt']


def test_hub_that_cannot_be_cloned(machine, capsys, onpath, tmp_path):
    hub, store = tmp_path / 'no-such-hub.git', tmp_path / 'store'
    code, out, err = run(['setup', '-y', '--store', str(store), '--hub', str(hub)], capsys)
    assert code == 2
    assert (f'git clone {hub} failed with exit status 128 (its message is above). Check the hub address and that '
            f'you can reach it (git ls-remote {hub}), then run llmpro setup --hub {hub} again') in err
    assert 'llmpro had changed nothing before this step.' in err and not store.exists()
    assert 'Fix the cause' not in err


def test_hub_that_is_not_a_store(machine, capsys, onpath, tmp_path):
    src, hub, store = tmp_path / 'src', tmp_path / 'hub.git', tmp_path / 'store'
    subprocess.run(['git', 'init', '-q', str(src)], check=True)
    (src / 'README').write_text('x\n')
    subprocess.run(['git', '-C', str(src), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(src), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'x'], check=True)
    subprocess.run(['git', 'clone', '-q', '--bare', str(src), str(hub)], check=True)
    code, out, err = run(['setup', '-y', '--store', str(store), '--hub', str(hub)], capsys)
    assert code == 2
    assert (f'{hub} was cloned to {store}, but it has no config.toml, so it is not an llmpro store. The clone is '
            f'left there. Check the hub address, move the clone away (mv {store} {store}.old), then run llmpro setup '
            f'--hub HUB again') in err


def test_uninstall_with_nothing_to_do(machine, capsys):
    assert run(['uninstall', '-y'], capsys)[0] == 0
    assert run(['uninstall', '-n'], capsys)[0] == 0
