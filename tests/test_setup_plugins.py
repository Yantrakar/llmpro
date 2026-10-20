"""setup and uninstall with the plugins' items: gitpro's setup, MCP registration, opencode config.
Everything runs in a temporary HOME with fake `gitpro`, `claude` and `codex` programs."""
import json
import os
import subprocess
import sys

import pytest

from llmpro import cli, register

SECRET = 'sk-fake-provider-key-0123456789'
WANT_MCP = {'type': 'local', 'command': ['llmpro', 'mcp'], 'enabled': True}
SETUP_ARGV = ['setup', '-n', '--json', '--agent', 'none']
APPLY_ARGV = ['setup', '-y', '--agent', 'none']


def run(argv, capsys):
    code = cli.main(argv)
    c = capsys.readouterr()
    return code, c.out, c.err


def undo_script(out):
    lines = [l for l in out.splitlines() if l.startswith('To undo: sh ')]
    assert len(lines) == 1, out
    return lines[0][len('To undo: sh '):]


def items(machine, capsys, *extra):
    code, out, _ = run(['--store', str(machine / '.agents'), 'setup', '-n', '--json', *extra], capsys)
    got = json.loads(out)
    return code, {i['name']: i for i in got['items']}, got


def fake_tool(folder, name, registered=(), fail_add=False, sleep=0):
    """A fake `claude` or `codex`: `mcp get|add|remove NAME` against a small state file; logs every argv."""
    body = f'''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
state_file = os.path.join(here, {name!r} + '.json')
state = json.load(open(state_file))
argv = sys.argv[1:]
with open(os.path.join(here, {name!r} + '.log'), 'a') as fh:
    fh.write(json.dumps(argv) + chr(10))
if argv[:2] == ['mcp', 'get']:
    time.sleep(state['sleep'])
    if argv[2] in state['registered'] or os.path.exists('.mcp.json'):  # like claude: a project's server counts
        print('found')
        sys.exit(0)
    print('No MCP server found', file=sys.stderr)
    sys.exit(1)
if argv[:2] == ['mcp', 'add']:
    if state['fail_add']:
        print('first line', file=sys.stderr)
        print('cannot write the config', file=sys.stderr)
        sys.exit(1)
    state['registered'].append(argv[argv.index('--') - 1])
elif argv[:2] == ['mcp', 'remove']:
    state['registered'] = [r for r in state['registered'] if r != argv[-1]]
else:
    sys.exit(2)
json.dump(state, open(state_file, 'w'))
'''
    path = folder / name
    path.write_text(f'#!{sys.executable}\n{body}')
    path.chmod(0o755)
    (folder / f'{name}.json').write_text(json.dumps({'registered': list(registered), 'fail_add': fail_add, 'sleep': sleep}))
    return path


def tool_calls(folder, name):
    log = folder / f'{name}.log'
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def mcp_changes(folder, name):
    return [c for c in tool_calls(folder, name) if c[:1] == ['mcp'] and c[:2] != ['mcp', 'get']]


def pending(*entries):
    return json.dumps({'ok': True, 'command': 'setup', 'dry_run': True,
                       'pending': [{'group': g, 'what': w} for g, w in entries]})


# ---------------------------------------------------------------- gitpro

def test_gitpro_set_up_is_present_and_not_run_again(machine, store, fake_gitpro, capsys, onpath):
    fake_gitpro.answer(SETUP_ARGV, pending(), exit=0)
    code, got, _ = items(machine, capsys, '--only', 'git')
    assert code == 0 and got['gitpro']['present']
    assert run(['--store', store, 'setup', '-y'], capsys)[0] == 0
    assert APPLY_ARGV not in fake_gitpro.calls()


def test_gitpro_pending_is_listed_then_run_once_after_the_core(machine, store, fake_gitpro, capsys, onpath):
    fake_gitpro.answer(SETUP_ARGV, pending(('hooks', 'install the global hooks'), ('guard', 'set the guard')), exit=1)
    fake_gitpro.answer(APPLY_ARGV, '', exit=0)
    code, out, _ = run(['--store', store, 'setup', '-n'], capsys)
    assert code == 1
    assert 'gitpro' in out and '2 to do' in out
    assert 'hooks: install the global hooks' in out and 'guard: set the guard' in out
    code, out, err = run(['--store', store, 'setup', '-y'], capsys)
    assert code == 0, err
    assert fake_gitpro.calls().count(APPLY_ARGV) == 1
    script = open(undo_script(out)).read()
    assert '# gitpro setup ran; to take it back run: gitpro uninstall' in script
    assert (machine / '.agents/.git/hooks/pre-commit').exists()  # the core items were applied too
    # undo lines are written in reverse, so the later gitpro note comes before the core hook lines
    assert script.index('gitpro uninstall') < script.index('pre-commit')


def test_gitpro_apply_failure_is_a_setup_error(machine, store, fake_gitpro, capsys, onpath):
    fake_gitpro.answer(SETUP_ARGV, pending(('hooks', 'x')), exit=1)
    fake_gitpro.answer(APPLY_ARGV, '', exit=3)
    code, out, err = run(['--store', store, 'setup', '-y', '--only', 'git'], capsys)
    assert code == 2 and 'gitpro setup failed with exit status 3; its output above says why.' in err
    assert 'then run llmpro setup again; it does only what is still missing.' in err
    assert 'gitpro uninstall' not in out


def test_gitpro_without_identities_is_not_missing(machine, store, fake_gitpro, capsys):
    fake_gitpro.answer(SETUP_ARGV, pending(('config', 'create gitpro.toml')), exit=1)
    code, got, _ = items(machine, capsys, '--only', 'git')
    assert code == 0
    assert got['gitpro']['present']
    assert 'run gitpro setup in a terminal once' in got['gitpro']['details']
    code, out, _ = run(['--store', store, 'setup', '-n', '--only', 'git'], capsys)
    assert 'no identities yet' in out


@pytest.mark.parametrize('answer', [('not json', 1), (json.dumps({'pending': []}), 1), (json.dumps({'pending': []}), 2)])
def test_gitpro_odd_answers_are_not_missing(machine, store, fake_gitpro, capsys, answer):
    fake_gitpro.answer(SETUP_ARGV, answer[0], exit=answer[1])
    code, got, _ = items(machine, capsys, '--only', 'git')
    assert code == 0 and got['gitpro']['present']
    code, out, _ = run(['--store', store, 'setup', '-n', '--only', 'git'], capsys)
    assert 'could not ask gitpro' in out


def test_gitpro_too_old_has_no_item_but_the_plugin_informs(machine, fake_gitpro, capsys):
    fake_gitpro.version('0.3.4', capabilities=())
    code, got, _ = items(machine, capsys, '--only', 'git')
    assert 'gitpro' not in got and 'plugin git' in got


def test_gitpro_missing_has_no_item(machine, capsys):
    code, got, _ = items(machine, capsys, '--only', 'git')
    assert 'gitpro' not in got and 'plugin git' in got


def test_gitpro_without_dry_run_capability_has_no_item(machine, fake_gitpro, capsys):
    fake_gitpro.version('0.4.0', capabilities=('setup', 'mcp'))
    code, got, _ = items(machine, capsys, '--only', 'git')
    assert 'gitpro' not in got


def test_uninstall_keeps_gitpros_setup(machine, store, fake_gitpro, capsys):
    fake_gitpro.answer(SETUP_ARGV, pending(), exit=0)
    code, out, _ = run(['--store', store, 'uninstall', '-n', '--json'], capsys)
    assert "gitpro's own setup is kept; gitpro uninstall removes it" in json.loads(out)['todo'][0]
    run(['--store', store, 'uninstall', '-y'], capsys)
    ran = [c for c in fake_gitpro.calls() if c[:1] in (['setup'], ['uninstall'])]
    assert ran == [c for c in ran if c == SETUP_ARGV]  # only the survey, never a change


# ---------------------------------------------------------------- claude code and codex

CASES = [
    ('claude', 'mcp: claude code', ['mcp', 'add', '--scope', 'user', 'llmpro', '--', 'llmpro', 'mcp'],
     'claude mcp remove --scope user llmpro'),
    ('codex', 'mcp: codex', ['mcp', 'add', 'llmpro', '--', 'llmpro', 'mcp'], 'codex mcp remove llmpro'),
]


@pytest.mark.parametrize('tool, item, add, undo_line', CASES)
def test_assistant_not_registered_is_added_with_an_undo(tool, item, add, undo_line, machine, store, tools_dir, capsys):
    fake_tool(tools_dir, tool)
    code, got, _ = items(machine, capsys, '--only', 'memory')
    assert code == 1 and not got[item]['present']
    code, out, err = run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)
    assert code == 0, err
    assert mcp_changes(tools_dir, tool) == [add]
    assert undo_line in open(undo_script(out)).read()
    assert items(machine, capsys, '--only', 'memory')[1][item]['present']
    assert run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)[0] == 0
    assert mcp_changes(tools_dir, tool) == [add]  # not added twice


@pytest.mark.parametrize('tool, item, add, undo_line', CASES)
def test_a_projects_mcp_json_does_not_count_as_registered(tool, item, add, undo_line, machine, store, tools_dir,
                                                          capsys, monkeypatch, tmp_path):
    # Setup run from inside a repo that ships a .mcp.json (llmpro's own, for one) must still register llmpro
    # for the user.
    project = tmp_path / 'project'
    project.mkdir()
    (project / '.mcp.json').write_text('{"mcpServers": {"llmpro": {"command": "llmpro", "args": ["mcp"]}}}')
    monkeypatch.chdir(project)
    fake_tool(tools_dir, tool)
    code, got, _ = items(machine, capsys, '--only', 'memory')
    assert not got[item]['present']
    assert run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)[0] == 0
    assert mcp_changes(tools_dir, tool) == [add]


@pytest.mark.parametrize('tool, item, add, undo_line', CASES)
def test_assistant_already_registered_is_in_place(tool, item, add, undo_line, machine, tools_dir, capsys):
    fake_tool(tools_dir, tool, registered=['llmpro'])
    code, got, _ = items(machine, capsys, '--only', 'memory')
    assert got[item]['present']
    assert mcp_changes(tools_dir, tool) == []


@pytest.mark.parametrize('tool, item, add, undo_line', CASES)
def test_assistant_with_gitpros_server_gets_a_hint(tool, item, add, undo_line, machine, tools_dir, capsys):
    fake_tool(tools_dir, tool, registered=['gitpro'])
    code, got, _ = items(machine, capsys, '--only', 'memory')
    text = "gitpro's own MCP server is registered too; llmpro serves its tools as git_*. To remove it: " \
           f'{tool} mcp remove gitpro'
    assert text in got[item]['details']
    assert not got[item]['present']


def test_no_assistant_means_no_item(machine, capsys):
    code, got, _ = items(machine, capsys, '--only', 'memory')
    assert 'mcp: claude code' not in got and 'mcp: codex' not in got


@pytest.mark.parametrize('tool, item, add, undo_line', CASES)
def test_assistant_add_failure_names_the_last_stderr_line(tool, item, add, undo_line, machine, store, tools_dir, capsys):
    fake_tool(tools_dir, tool, fail_add=True)
    code, out, err = run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)
    assert code == 2 and 'cannot write the config' in err and 'first line' not in err


def test_assistant_that_hangs_counts_as_not_registered(machine, tools_dir, capsys, monkeypatch):
    fake_tool(tools_dir, 'claude', registered=['llmpro'], sleep=3)
    monkeypatch.setattr(register, 'TIMEOUT', 0.3)
    code, got, _ = items(machine, capsys, '--only', 'memory')
    it = got['mcp: claude code']
    assert not it['present'] and any('timed out' in d for d in it['details'])


def test_outside_commands_get_no_input(tmp_path):
    code, err = register.run_tool([sys.executable, '-c', 'import sys; sys.exit(0 if sys.stdin.read() == "" else 5)'])
    assert code == 0


# ---------------------------------------------------------------- opencode

def oc_file(machine, name='opencode.json'):
    return machine / '.config/opencode' / name


def want_instructions(machine):
    return str(machine / '.agents/AGENTS.md')


def test_opencode_no_file_is_created_and_undone(machine, store, capsys):
    code, out, err = run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)
    assert code == 0, err
    data = json.loads(oc_file(machine).read_text())
    assert data == {'$schema': 'https://opencode.ai/config.json', 'instructions': [want_instructions(machine)],
                    'mcp': {'llmpro': WANT_MCP}}
    assert oc_file(machine).read_text().endswith('}\n')
    assert items(machine, capsys, '--only', 'memory')[1]['opencode config']['present']
    subprocess.run(['sh', undo_script(out)], check=True)
    assert not oc_file(machine).exists()


def test_opencode_plain_json_keeps_other_keys_and_order(machine, store, capsys):
    original = {'provider': {'x': {'options': {'apiKey': SECRET}}}, 'theme': 'dark', 'instructions': ['mine.md'],
                'mcp': {'other': {'type': 'local', 'command': ['x']}}}
    oc_file(machine).write_text(json.dumps(original))
    code, out, err = run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)
    assert code == 0, err
    data = json.loads(oc_file(machine).read_text())
    assert list(data) == ['provider', 'theme', 'instructions', 'mcp']
    assert data['provider'] == original['provider'] and data['theme'] == 'dark'
    assert data['instructions'] == ['mine.md', want_instructions(machine)]
    assert data['mcp'] == {'other': original['mcp']['other'], 'llmpro': WANT_MCP}
    assert oc_file(machine).read_text() == json.dumps(data, indent=2) + '\n'
    backups = [p for p in (machine / 'state/llmpro').iterdir() if p.name.startswith('backup-')]
    assert len(backups) == 1 and (backups[0].stat().st_mode & 0o777) == 0o700
    assert json.loads((backups[0] / 'opencode.json').read_text()) == original
    subprocess.run(['sh', undo_script(out)], check=True)
    assert json.loads(oc_file(machine).read_text()) == original
    assert SECRET not in out + err


def skill_left_alone(store):
    """A hand-edited copy of the llmpro skill, which setup leaves alone, so these tests see only opencode."""
    folder = os.path.join(store, 'skills', 'llmpro')
    os.makedirs(folder)
    with open(os.path.join(folder, 'SKILL.md'), 'w') as fh:
        fh.write('---\nname: llmpro\n---\nmy own notes\n')


def test_opencode_already_set_is_not_rewritten(machine, store, capsys):
    text = json.dumps({'instructions': [want_instructions(machine)], 'mcp': {'llmpro': WANT_MCP}}, indent=4)
    oc_file(machine).write_text(text)
    os.utime(oc_file(machine), (1000, 1000))
    skill_left_alone(store)
    code, got, _ = items(machine, capsys, '--only', 'memory')
    assert got['opencode config']['present']
    code, out, err = run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)
    assert code == 0 and 'To undo' not in out
    assert oc_file(machine).read_text() == text and os.stat(oc_file(machine)).st_mtime == 1000


def test_opencode_json_with_one_value_missing_gets_only_that(machine, store, capsys):
    oc_file(machine).write_text(json.dumps({'instructions': [want_instructions(machine)]}))
    assert run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)[0] == 0
    data = json.loads(oc_file(machine).read_text())
    assert data['instructions'] == [want_instructions(machine)] and data['mcp'] == {'llmpro': WANT_MCP}


def test_opencode_jsonc_is_left_alone_and_the_lines_are_printed(machine, store, capsys):
    text = '// my settings\n{\n  "theme": "dark", // trailing\n  "key": "' + SECRET + '",\n}\n'
    oc_file(machine, 'opencode.jsonc').write_text(text)
    skill_left_alone(store)
    code, got, _ = items(machine, capsys, '--only', 'memory')
    assert not got['opencode config']['present']
    code, out, err = run(['--store', store, 'setup', '-n', '--only', 'memory'], capsys)
    assert SECRET not in out + err
    code, out, err = run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)
    assert code == 0
    assert oc_file(machine, 'opencode.jsonc').read_text() == text
    assert not oc_file(machine).exists()
    assert want_instructions(machine) in err and '"llmpro"' in err and '"mcp"' in err
    assert SECRET not in out + err
    assert 'To undo' not in out  # nothing was changed, so nothing to undo


def test_opencode_json_is_chosen_before_jsonc(machine, store, capsys):
    oc_file(machine).write_text('{"theme": "dark"}')
    oc_file(machine, 'opencode.jsonc').write_text('// c\n{}\n')
    assert run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)[0] == 0
    assert 'llmpro' in json.loads(oc_file(machine).read_text())['mcp']
    assert oc_file(machine, 'opencode.jsonc').read_text() == '// c\n{}\n'


def test_no_opencode_folder_means_no_item(machine, capsys):
    (machine / '.config/opencode').rmdir()
    code, got, _ = items(machine, capsys, '--only', 'memory')
    assert 'opencode config' not in got


def test_unexpected_shape_is_left_for_hand_editing(machine, store, capsys):
    oc_file(machine).write_text('[1, 2]')
    code, out, err = run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)
    assert code == 0 and oc_file(machine).read_text() == '[1, 2]'


# ---------------------------------------------------------------- uninstall

def test_uninstall_lists_and_runs_the_removals(machine, store, tools_dir, capsys, onpath):
    fake_tool(tools_dir, 'claude')
    fake_tool(tools_dir, 'codex')
    original = {'provider': {'x': {'apiKey': SECRET}}, 'theme': 'dark'}
    oc_file(machine).write_text(json.dumps(original, indent=2) + '\n')
    assert run(['--store', store, 'setup', '-y'], capsys)[0] == 0
    code, out, err = run(['--store', store, 'uninstall', '-n'], capsys)
    assert code == 1
    assert 'claude mcp remove --scope user llmpro' in out
    assert 'codex mcp remove llmpro' in out
    assert 'opencode.json' in out
    assert SECRET not in out + err
    before = len(mcp_changes(tools_dir, 'claude'))
    code, out, err = run(['--store', store, 'uninstall', '-y'], capsys)
    assert code == 0, err
    assert mcp_changes(tools_dir, 'claude')[before:] == [['mcp', 'remove', '--scope', 'user', 'llmpro']]
    assert mcp_changes(tools_dir, 'codex')[-1] == ['mcp', 'remove', 'llmpro']
    assert json.loads(oc_file(machine).read_text()) == original
    assert SECRET not in out + err
    script = open(undo_script(out)).read()
    assert 'claude mcp add --scope user llmpro -- llmpro mcp' in script
    assert run(['--store', store, 'uninstall', '-n'], capsys)[0] == 0
    env = {**os.environ, 'PATH': str(tools_dir) + os.pathsep + os.environ['PATH']}
    subprocess.run(['sh', undo_script(out)], check=True, env=env)
    assert 'llmpro' in json.loads(oc_file(machine).read_text())['mcp']


def test_uninstall_removes_a_file_setup_created(machine, store, capsys, onpath):
    assert run(['--store', store, 'setup', '-y'], capsys)[0] == 0
    assert oc_file(machine).exists()
    assert run(['--store', store, 'uninstall', '-y'], capsys)[0] == 0
    assert not oc_file(machine).exists()


def test_uninstall_of_jsonc_prints_what_to_take_out(machine, store, capsys):
    text = '// c\n{"instructions": ["' + want_instructions(machine) + '"], "mcp": {"llmpro": {}}, }\n'
    oc_file(machine, 'opencode.jsonc').write_text(text)
    code, out, err = run(['--store', store, 'uninstall', '-n'], capsys)
    assert code == 1 and 'opencode.jsonc' in out
    code, out, err = run(['--store', store, 'uninstall', '-y'], capsys)
    assert code == 0 and oc_file(machine, 'opencode.jsonc').read_text() == text
    assert want_instructions(machine) in err and 'To undo' not in out
