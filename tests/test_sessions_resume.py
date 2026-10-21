"""Package 17: `llmpro resume TARGET` continues a session. Every tool is a small script in tools_dir that
records its argv, its folder and the NAMES of its environment variables; nothing here starts a real claude,
copilot, opencode or ssh, reads the real home, or touches VS Code's settings."""
import builtins
import io
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from llmpro import cli, sessions
from llmpro import store as cfg
from llmpro.sessions import launch, remote, seen

SRC = os.path.dirname(os.path.dirname(os.path.abspath(sessions.__file__)))
AID = 'aaaaaaaa-1111-4111-8111-111111111111'
BID = 'bbbbbbbb-2222-4222-8222-222222222222'
CID = 'cccccccc-3333-4333-8333-333333333333'

FAKE_TOOL = '''#!{python}
import json, os, signal, sys
here = os.path.dirname(os.path.abspath(__file__))
conf = json.load(open(os.path.join(here, '{name}.json'))) if os.path.exists(os.path.join(here, '{name}.json')) else {{}}
with open(os.path.join(here, '{name}.log'), 'a') as fh:
    fh.write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'env': sorted(os.environ),
        'sigint_default': signal.getsignal(signal.SIGINT) is signal.default_int_handler,
        'sigquit_default': signal.getsignal(signal.SIGQUIT) is signal.SIG_DFL}}) + '\\n')
for rel, text in conf.get('write', {{}}).items():
    path = os.path.expanduser(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as out:
        out.write(text)
sys.exit(conf.get('exit', 0))
'''

FAKE_SSH = '''#!{python}
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, 'ssh.log'), 'a') as fh:
    fh.write(json.dumps(sys.argv[1:]) + '\\n')
sys.exit(int(os.environ.get('FAKE_SSH_EXIT', '0')))
'''


class Tools:
    def __init__(self, folder):
        self.folder = folder

    def make(self, name, template=FAKE_TOOL, **conf):
        path = self.folder / name
        path.write_text(template.format(python=sys.executable, name=name))
        path.chmod(0o755)
        (self.folder / f'{name}.json').write_text(json.dumps(conf))

    def calls(self, name):
        log = self.folder / f'{name}.log'
        if not log.exists():
            return []
        return [json.loads(x) for x in log.read_text().splitlines()]


@pytest.fixture
def tools(tools_dir):
    return Tools(tools_dir)


@pytest.fixture(autouse=True)
def state(home, tmp_path, monkeypatch):
    """A temporary HOME and XDG_STATE_HOME, and no editor variables from the real environment."""
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    for name in list(os.environ):
        if name.startswith(('VSCODE_', 'CLAUDE', 'COPILOT_', 'OPENCODE')) or name in ('TERM_PROGRAM', 'AI_AGENT'):
            monkeypatch.delenv(name, raising=False)
    sessions.begin()
    yield tmp_path / 'state'
    cfg.STORE = ''


def row(**over):
    base = dict(tool='claude-code', machine=cfg.MACHINE, id=AID, name='demo', folder='/nonexistent', state='needs-reply',
                since=10, line='', pid=None, where='', version='2.1', local_only=False, error='')
    base.update(over)
    return sessions.Row(**base)


def run(args, capsys):
    code = cli.main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


def config(store, text):
    with open(f'{store}/config.toml', 'a', encoding='utf-8') as fh:
        fh.write('\n' + text)


def listing(monkeypatch, *rows):
    """Make the listing (and the second look inside launch.run) return these rows."""
    monkeypatch.setattr(sessions, 'local_rows', lambda line=True, machine=None: list(rows))


# ---------------------------------------------------------------- resolve

def test_resolve_exact_id_over_name_over_prefix():
    a = row(id='alpha-0001', name='other')
    b = row(id='beta-0002', name='alpha-0001')
    c = row(id='alpha-0003', name='gamma1')
    assert sessions.resolve('alpha-0001', [a, b, c]) is a
    d = row(id='omega1-xyz', name='x')
    e = row(id='zzzzzz-1', name='omega1')
    assert sessions.resolve('omega1', [d, e]) is e
    assert sessions.resolve('omega1-', [d, e]) is d


def test_resolve_prefix_needs_six_characters():
    r = row(id='abc12345-xx')
    assert sessions.resolve('abc123', [r]) is r
    with pytest.raises(sessions.TargetError, match='at least 6'):
        sessions.resolve('abc12', [r])
    with pytest.raises(sessions.TargetError, match='no session'):
        sessions.resolve('nothing-here', [r])


def test_resolve_two_matches_lists_full_targets():
    a = row(id='abcdef-1', machine='work')
    b = row(id='abcdef-2', machine='laptop')
    with pytest.raises(sessions.TargetError) as err:
        sessions.resolve('abcdef', [a, b])
    text = str(err.value)
    assert 'claude-code:abcdef-1@work' in text and 'claude-code:abcdef-2@laptop' in text
    same = row(id='abcdef-3', name='twin')
    with pytest.raises(sessions.TargetError, match='twin'):
        sessions.resolve('twin', [same, row(id='abcdef-4', name='twin')])


def test_resolve_tool_names_short_and_long():
    a = row(id='same-aaaa', name='shared')
    b = row(tool='copilot-cli', id='same-bbbb', name='shared')
    c = row(tool='opencode', id='same-cccc', name='shared')
    assert sessions.resolve('claude:shared', [a, b, c]) is a
    assert sessions.resolve('claude-code:shared', [a, b, c]) is a
    assert sessions.resolve('copilot:shared', [a, b, c]) is b
    assert sessions.resolve('copilot-cli:shared', [a, b, c]) is b
    assert sessions.resolve('opencode:shared', [a, b, c]) is c
    with pytest.raises(sessions.TargetError):
        sessions.resolve('shared', [a, b, c])


def test_resolve_machine_and_unknown_suffix():
    a = row(id='same-aaaa', name='shared', machine='work')
    b = row(id='same-bbbb', name='shared', machine='laptop')
    assert sessions.resolve('shared@laptop', [a, b]) is b
    c = row(id='same-cccc', name='who@example')
    assert sessions.resolve('who@example', [c, a]) is c  # example is not a machine of any row


def test_resolve_number_only_when_shown():
    a, b = row(id='one-aaaaa'), row(id='two-bbbbb')
    assert sessions.resolve('2', [a, b], shown=[a, b]) is b
    with pytest.raises(sessions.TargetError, match='no line 3'):
        sessions.resolve('3', [a, b], shown=[a, b])
    with pytest.raises(sessions.TargetError):
        sessions.resolve('2', [a, b])  # from the command line a number is a name or an id


def test_resolve_closed_session_from_seen(state):
    seen.add(row(id=BID, name='closed one', folder='/work/x'))
    got = sessions.resolve('closed one', [])
    assert (got.id, got.folder, got.pid) == (BID, '/work/x', None)
    assert sessions.resolve('bbbbbbbb', []).id == BID
    # an open row of the same session wins and the seen copy is not offered twice
    open_row = row(id=BID, name='closed one', pid=7)
    assert sessions.resolve('closed one', [open_row]) is open_row


def test_resolve_closed_session_from_claude_transcript(home):
    d = home / '.claude' / 'projects' / '-work-x'
    d.mkdir(parents=True)
    (d / f'{CID}.jsonl').write_text('{"type": "summary"}\n{"cwd": "/work/first"}\n{"cwd": "/work/later"}\n')
    got = sessions.resolve('cccccccc', [])
    assert (got.tool, got.id, got.folder, got.pid) == ('claude-code', CID, '/work/first', None)
    assert sessions.resolve('claude:' + CID, []).id == CID
    with pytest.raises(sessions.TargetError):
        sessions.resolve('copilot:cccccccc', [])


def test_resolve_closed_session_from_copilot_state(home):
    d = home / '.copilot' / 'session-state' / BID
    d.mkdir(parents=True)
    (d / 'events.jsonl').write_text(json.dumps({'type': 'session.start', 'data': {'context': {'cwd': '/work/c'}}}) + '\n')
    got = sessions.resolve('bbbbbbbb', [])
    assert (got.tool, got.id, got.folder) == ('copilot-cli', BID, '/work/c')


def test_resolve_glob_characters_in_a_key_match_nothing(home):
    (home / '.claude' / 'projects' / 'p').mkdir(parents=True)
    (home / '.claude' / 'projects' / 'p' / f'{AID}.jsonl').write_text('{"cwd": "/w"}\n')
    with pytest.raises(sessions.TargetError):
        sessions.resolve('aaaaaa*', [])


# ---------------------------------------------------------------- seen

def test_seen_keeps_no_line_and_is_private(state):
    seen.record([row(line='secret words', name='n')])
    path = state / 'llmpro' / 'seen.json'
    assert (path.stat().st_mode & 0o777) == 0o600
    text = path.read_text()
    assert 'secret words' not in text and 'line' not in text
    got = json.loads(text)
    assert len(got['sessions']) == 1


def test_seen_writes_only_when_something_changed(state, monkeypatch):
    writes = []
    real = os.replace
    monkeypatch.setattr(os, 'replace', lambda a, b: writes.append(b) or real(a, b))
    r = row()
    seen.record([r])
    seen.record([r])
    assert len(writes) == 1
    seen.record([row(name='renamed')])
    assert len(writes) == 2


def test_seen_keeps_500_newest(state):
    for i in range(520):
        seen.add(row(id=f'id-{i:04d}', name=f'n{i}'))
        # a distinct, increasing time per entry
    data = json.loads((state / 'llmpro' / 'seen.json').read_text())['sessions']
    assert len(data) == 500
    ids = {e['id'] for e in data}
    assert 'id-0519' in ids and 'id-0000' not in ids


def test_seen_leaves_out_local_only_and_unreadable_rows(state):
    seen.record([row(local_only=True), row(id=BID, state='not-readable'), row(id='bad id!')])
    assert seen.lookup('anything') == []


def test_seen_lookup_skips_open_ones(state):
    seen.add(row(id=AID, name='x'))
    seen.add(row(id=BID, name='y'))
    assert [r.id for r in seen.lookup(AID[:8], exclude={(row().tool, row().machine, AID)})] == []
    assert [r.id for r in seen.lookup(BID[:8])] == [BID]


def test_seen_ignores_a_damaged_file(state):
    (state / 'llmpro').mkdir(parents=True)
    (state / 'llmpro' / 'seen.json').write_text('{not json')
    assert seen.lookup('abc') == []
    seen.add(row())
    assert len(seen.lookup(AID)) == 1


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_terminal_listing_records_and_json_and_dry_run_do_not(store, state, monkeypatch, capsys):
    listing(monkeypatch, row(pid=5, where='terminal'))
    path = state / 'llmpro' / 'seen.json'
    assert run(['resume', '--store', store, '--json'], capsys)[0] == 0
    assert run(['resume', '--store', store, '-n'], capsys)[0] == 0
    assert run(['resume', '--store', store], capsys)[0] == 0  # not a terminal
    assert not path.exists()
    monkeypatch.setattr(sys, 'stdin', Tty(''))
    monkeypatch.setattr(sys, 'stdout', Tty())
    monkeypatch.setattr(builtins, 'input', lambda prompt='': '')
    assert cli.main(['resume', '--store', store]) == 0
    assert path.exists() and len(json.loads(path.read_text())['sessions']) == 1


# ---------------------------------------------------------------- env

def test_env_removes_every_nesting_variable_and_keeps_the_rest():
    env = {'PATH': '/bin', 'HOME': '/h', 'LANG': 'C', 'SSH_AUTH_SOCK': '/s', 'HTTPS_PROXY': 'p', 'TERM': 'xterm',
           'CLAUDECODE': '1', 'CLAUDE_PID': '1', 'CLAUDE_EFFORT': 'high', 'CLAUDE_CODE_ENTRYPOINT': 'cli',
           'CLAUDE_CODE_SSE_PORT': '1', 'AI_AGENT': 'x', 'COPILOT_CLI': '1', 'COPILOT_ALLOW_ALL': '1',
           'OPENCODE': '1', 'OPENCODE_PID': '2', 'VSCODE_PID': '3', 'VSCODE_IPC_HOOK_CLI': '/x',
           'GTK_IM_MODULE_VSCODE_SNAP_ORIG': 'a', 'LLMPRO_TOOL_PATH': '/t', 'LLMPRO_SEND_HOPS': '1', 'CLAUDE_OTHER': 'k'}
    got = launch.env(env)
    assert set(got) == {'PATH', 'HOME', 'LANG', 'SSH_AUTH_SOCK', 'HTTPS_PROXY', 'TERM', 'LLMPRO_SEND_HOPS', 'CLAUDE_OTHER'}
    assert got is not env and 'CLAUDECODE' in env  # the original is not changed


def test_env_term_program_kept_unless_vscode():
    assert launch.env({'TERM_PROGRAM': 'iTerm', 'TERM_PROGRAM_VERSION': '3'}) == {'TERM_PROGRAM': 'iTerm',
                                                                                   'TERM_PROGRAM_VERSION': '3'}
    assert launch.env({'TERM_PROGRAM': 'vscode', 'TERM_PROGRAM_VERSION': '1.9'}) == {}


def test_env_agent_and_git_variables_depend_on_the_tool():
    env = {'AGENT': 'a', 'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'k', 'GIT_CONFIG_VALUE_0': 'v',
           'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_GLOBAL': '/g'}
    assert launch.env(env) == env
    assert launch.env(dict(env, OPENCODE='1')) == {k: v for k, v in env.items() if k != 'AGENT'}
    only_git = {k: v for k, v in env.items() if k != 'AGENT'}
    assert launch.env(dict(env, COPILOT_CLI='1')) == {'AGENT': 'a', 'GIT_CONFIG_GLOBAL': '/g'}
    assert only_git  # keeps the comparison honest


def test_env_copied_from_a_vscode_terminal():
    env = {'TERM_PROGRAM': 'vscode', 'TERM_PROGRAM_VERSION': '1', 'VSCODE_GIT_ASKPASS_NODE': '/n',
           'VSCODE_INJECTION': '1', 'VSCODE_GIT_IPC_HANDLE': '/h', 'PATH': '/bin', 'COLORTERM': 'truecolor'}
    assert launch.env(env) == {'PATH': '/bin', 'COLORTERM': 'truecolor'}


def test_nesting_is_one_documented_constant():
    assert launch.NESTING
    src = open(launch.__file__, encoding='utf-8').read()
    assert 'NESTING' in src and chr(0x2014) not in src


# ---------------------------------------------------------------- command

def test_command_per_tool(tools):
    for name in ('claude', 'copilot', 'opencode'):
        tools.make(name)
    c = launch.command(row())
    assert c[1:] == ['--resume', AID] and os.path.basename(c[0]) == 'claude'
    c = launch.command(row(tool='copilot-cli'))
    assert c[1:] == ['--resume', AID] and os.path.basename(c[0]) == 'copilot'
    c = launch.command(row(tool='opencode', id='ses_abc'))
    assert c[1:] == ['-s', 'ses_abc'] and os.path.basename(c[0]) == 'opencode'


def test_command_missing_tool_is_named():
    with pytest.raises(launch.Error, match='claude'):
        launch.command(row())


# ---------------------------------------------------------------- lock

def test_lock_is_held_and_released(state):
    r = row()
    with launch.lock(r):
        path = state / 'llmpro' / 'live' / f'claude-code-{AID}.lock'
        assert path.exists() and ((state / 'llmpro' / 'live').stat().st_mode & 0o777) == 0o700
        with pytest.raises(launch.Busy, match='busy with another llmpro'):
            with launch.lock(r):
                pass
    with launch.lock(r):
        pass


def test_lock_second_holder_in_another_process(state):
    code = ('import sys; sys.path.insert(0, sys.argv[1])\n'
            'from llmpro import sessions\nfrom llmpro.sessions import launch\n'
            'r = sessions.Row("claude-code", "m", sys.argv[2], "", "/", "unknown", None, "", None, "", "", False)\n'
            'try:\n    with launch.lock(r): print("got it")\nexcept launch.Busy as e: print("busy:", e)\n')
    r = row()
    env = dict(os.environ, XDG_STATE_HOME=str(state))
    with launch.lock(r):
        out = subprocess.run([sys.executable, '-c', code, SRC, AID], capture_output=True, text=True, env=env)
    assert out.stdout.startswith('busy: ') and 'busy with another llmpro' in out.stdout, out.stderr
    out = subprocess.run([sys.executable, '-c', code, SRC, AID], capture_output=True, text=True, env=env)
    assert out.stdout.strip() == 'got it'


@pytest.mark.parametrize('bad', ['', '../x', 'a/b', 'a b', '.', 'a\nb'])
def test_lock_refuses_a_bad_id(state, bad):
    with pytest.raises(launch.Error, match='not a session id'):
        with launch.lock(row(id=bad)):
            pass
    assert not (state / 'llmpro' / 'live').exists() or not list((state / 'llmpro' / 'live').iterdir())


def test_lock_refuses_an_unknown_tool(state):
    with pytest.raises(launch.Error):
        with launch.lock(row(tool='../x')):
            pass


# ---------------------------------------------------------------- run

@pytest.fixture
def folder(tmp_path):
    d = tmp_path / 'work' / 'proj'
    d.mkdir(parents=True)
    return d


def test_run_passes_cwd_env_and_status(tools, folder, state, monkeypatch, capsys):
    tools.make('claude', exit=3)
    monkeypatch.setenv('CLAUDECODE', '1')
    monkeypatch.setenv('VSCODE_PID', '9')
    monkeypatch.setenv('TERM_PROGRAM', 'vscode')
    monkeypatch.setenv('LLMPRO_TOOL_PATH_KEEP_ME', 'x')
    monkeypatch.setenv('SSH_AUTH_SOCK', '/s')
    listing(monkeypatch)
    before = signal.getsignal(signal.SIGINT)
    status = launch.run(row(folder=str(folder)))
    assert status == 3
    (call,) = tools.calls('claude')
    assert call['argv'] == ['--resume', AID]
    assert os.path.realpath(call['cwd']) == os.path.realpath(folder)
    names = set(call['env'])
    assert not names & {'CLAUDECODE', 'VSCODE_PID', 'TERM_PROGRAM', 'LLMPRO_TOOL_PATH', 'LLMPRO_TOOL_PATH_KEEP_ME'}
    assert {'PATH', 'HOME', 'SSH_AUTH_SOCK'} <= names
    assert call['sigint_default'] and call['sigquit_default']
    assert signal.getsignal(signal.SIGINT) is before
    out, err = capsys.readouterr()
    assert out == f'continuing claude-code demo in {folder}\n'
    assert err == ''
    assert seen.lookup(AID)[0].folder == str(folder)  # seen.add


def test_run_uses_the_id_when_there_is_no_name(tools, folder, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch)
    launch.run(row(folder=str(folder), name=''))
    assert capsys.readouterr().out == f'continuing claude-code {AID} in {folder}\n'


def test_run_takes_the_lock_unless_one_is_passed(tools, folder, state, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch)
    r = row(folder=str(folder))
    with launch.lock(r):
        assert launch.run(r) == 1
        assert 'busy with another llmpro' in capsys.readouterr().err
        assert tools.calls('claude') == []
    with launch.lock(r) as held:
        assert launch.run(r, lock=held) == 0
    assert len(tools.calls('claude')) == 1


def test_run_refuses_a_session_that_became_open(tools, folder, monkeypatch, capsys):
    tools.make('claude')
    r = row(folder=str(folder))
    listing(monkeypatch, row(folder=str(folder), pid=777, where='vscode', state='needs-reply'))
    assert launch.run(r) == 1
    err = capsys.readouterr().err
    assert 'is open in vscode on' in err and '(pid 777)' in err and 'close it there first' in err
    assert tools.calls('claude') == []


def test_run_folder_gone(tools, tmp_path, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch)
    gone = tmp_path / 'gone'
    assert launch.run(row(folder=str(gone))) == 1
    assert f'{gone} is gone; the session cannot be continued there' in capsys.readouterr().err
    assert tools.calls('claude') == []


def test_run_missing_tool(folder, monkeypatch, capsys):
    listing(monkeypatch)
    assert launch.run(row(folder=str(folder))) == 1
    assert 'claude' in capsys.readouterr().err


def test_run_container_row_refused(tools, folder, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch)
    assert launch.run(row(folder=str(folder), where='container')) == 1
    assert 'runs in a container; continue it there' in capsys.readouterr().err
    assert tools.calls('claude') == []


def test_run_opencode_has_no_pid_and_is_continued(tools, folder, monkeypatch):
    tools.make('opencode', exit=0)
    listing(monkeypatch, row(tool='opencode', id='ses_1', folder=str(folder), state='working', where='terminal'))
    assert launch.run(row(tool='opencode', id='ses_1', folder=str(folder), state='working', where='terminal')) == 0
    assert tools.calls('opencode')[0]['argv'] == ['-s', 'ses_1']


def test_run_reports_a_changed_host_file_and_leaves_it(tools, folder, home, monkeypatch, capsys):
    keys = home / '.config' / 'Code' / 'User' / 'keybindings.json'
    keys.parent.mkdir(parents=True)
    keys.write_text('[]\n')
    tools.make('claude', write={str(keys): '[{"key": "shift+enter"}]\n', str(keys) + '.backup': '[]\n'})
    listing(monkeypatch)
    assert launch.run(row(folder=str(folder))) == 0
    err = capsys.readouterr().err
    assert f'warning: {keys} changed while claude-code ran' in err
    assert f'see the change: diff {keys}.backup {keys}' in err
    assert f'to put it back: mv -f {keys}.backup {keys}' in err
    assert keys.read_text() == '[{"key": "shift+enter"}]\n'
    assert (keys.parent / 'keybindings.json.backup').read_text() == '[]\n'


def test_run_quiet_when_host_files_did_not_change(tools, folder, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch)
    launch.run(row(folder=str(folder)))
    assert capsys.readouterr().err == ''


# ---------------------------------------------------------------- host files

def test_host_snapshot_and_report(home):
    snap = launch.host_snapshot()
    assert len(snap) >= 4 and all(v is None for v in snap.values())
    assert any(p.endswith('Code/User/keybindings.json') for p in snap)
    assert any(p.endswith('Code - Insiders/User/settings.json') for p in snap)
    assert all(p.startswith(str(home)) for p in snap)
    settings = home / '.config' / 'Code - Insiders' / 'User' / 'settings.json'
    settings.parent.mkdir(parents=True)
    started = time.time()
    time.sleep(0.05)  # file times are a little coarse
    settings.write_text('{}')
    lines = launch.host_report(snap, started, 'copilot-cli')
    assert lines == [f'warning: {settings} changed while copilot-cli ran']
    assert launch.host_report(launch.host_snapshot(), started) == []
    backup = settings.with_name('settings.json.bak')
    backup.write_text('old')
    settings.write_text('{"a": 1}')
    lines = launch.host_report(launch.host_snapshot() | {str(settings): 'stale'}, started, 'x')
    q = lambda p: "'" + str(p) + "'"  # the folder name holds spaces
    assert lines[1] == f'see the change: diff {q(backup)} {q(settings)}'
    assert lines[2] == f'to put it back: mv -f {q(backup)} {q(settings)}'


def test_host_report_ignores_an_old_backup(home):
    keys = home / '.config' / 'Code' / 'User' / 'keybindings.json'
    keys.parent.mkdir(parents=True)
    keys.write_text('[]')
    (keys.parent / 'keybindings.json.backup').write_text('older')
    started = time.time() + 5
    before = launch.host_snapshot()
    keys.write_text('[1]')
    lines = launch.host_report(before, started, 't')
    assert len(lines) == 1 and lines[0].startswith('warning: ')


# ---------------------------------------------------------------- the command

def test_resume_target_runs_the_tool(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', exit=4)
    listing(monkeypatch, row(folder=str(folder), name='demo'))
    code, out, err = run(['resume', '--store', store, 'demo'], capsys)
    assert code == 4 and f'continuing claude-code demo in {folder}' in out
    assert tools.calls('claude')[0]['argv'] == ['--resume', AID]


def test_resume_target_open_session_refused(tools, folder, store, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch, row(folder=str(folder), name='demo', pid=321, where='terminal'))
    code, out, err = run(['resume', '--store', store, 'demo'], capsys)
    assert code == 1 and out == ''
    assert f'demo is open in terminal on {cfg.MACHINE} (pid 321); close it there first' in err
    assert tools.calls('claude') == []


def test_resume_target_container_refused_even_when_closed(tools, folder, store, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch, row(folder=str(folder), name='demo', where='container', pid=None))
    code, out, err = run(['resume', '--store', store, 'demo'], capsys)
    assert code == 1 and 'demo runs in a container; continue it there' in err
    assert tools.calls('claude') == []


def test_resume_target_errors_exit_2(store, monkeypatch, capsys):
    listing(monkeypatch, row(name='a'), row(id=BID, name='a'))
    code, out, err = run(['resume', '--store', store, 'a'], capsys)
    assert code == 2 and 'claude-code:' + AID in err and 'claude-code:' + BID in err
    code, _, err = run(['resume', '--store', store, 'zzz'], capsys)
    assert code == 2 and 'no session' in err


def test_resume_target_finds_a_closed_session_in_the_transcripts(tools, folder, home, store, monkeypatch, capsys):
    tools.make('claude')
    d = home / '.claude' / 'projects' / '-p'
    d.mkdir(parents=True)
    (d / f'{CID}.jsonl').write_text(json.dumps({'cwd': str(folder)}) + '\n')
    listing(monkeypatch)
    code, out, err = run(['resume', '--store', store, 'cccccccc'], capsys)
    assert code == 0 and tools.calls('claude')[0]['argv'] == ['--resume', CID]


def test_resume_dry_run_prints_and_runs_nothing(tools, folder, state, store, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch, row(folder=str(folder), name='demo'))
    code, out, err = run(['resume', '--store', store, '-n', 'demo'], capsys)
    assert code == 0 and tools.calls('claude') == []
    assert cfg.MACHINE in out and str(folder) in out and '--resume ' + AID in out
    assert not (state / 'llmpro' / 'live').exists()


def test_resume_dry_run_still_refuses_an_open_session(tools, folder, store, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch, row(folder=str(folder), name='demo', pid=1, where='ssh'))
    assert run(['resume', '--store', store, '-n', 'demo'], capsys)[0] == 1


def test_prompt_continues_several_in_turn(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', exit=0)
    listing(monkeypatch, row(folder=str(folder), name='demo', pid=None))
    answers = iter(['1', 'demo', ''])
    shown = []
    monkeypatch.setattr(sys, 'stdin', Tty(''))
    monkeypatch.setattr(sys, 'stdout', Tty())
    monkeypatch.setattr(builtins, 'input', lambda prompt='': shown.append(prompt) or next(answers))
    assert cli.main(['resume', '--store', store]) == 0
    assert len(tools.calls('claude')) == 2
    assert len(shown) == 3


def test_prompt_shows_a_refusal_and_asks_again(tools, folder, store, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch, row(folder=str(folder), name='demo', pid=9, where='terminal'))
    answers = iter(['1', ''])
    monkeypatch.setattr(sys, 'stdin', Tty(''))
    monkeypatch.setattr(sys, 'stdout', Tty())
    monkeypatch.setattr(builtins, 'input', lambda prompt='': next(answers))
    assert cli.main(['resume', '--store', store]) == 0
    assert 'is open in terminal' in capsys.readouterr().err
    assert tools.calls('claude') == []


def test_capability_is_listed():
    assert 'resume.target' in cli.CAPABILITIES and list(cli.CAPABILITIES) == sorted(cli.CAPABILITIES)


# ---------------------------------------------------------------- remote

@pytest.fixture
def ssh(tools_dir, store):
    (tools_dir / 'ssh').write_text(FAKE_SSH.format(python=sys.executable))
    (tools_dir / 'ssh').chmod(0o755)
    config(store, '[machines]\nwork = "me@work.example.com"\n')
    return tools_dir


def ssh_calls(folder):
    log = folder / 'ssh.log'
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def test_remote_argv():
    m = cfg.Machine('work', 'me@work.example.com', '~/.local/bin/llmpro', '')
    argv = launch.remote_argv(m, 'claude:my name')
    assert argv[:4] == ['ssh', '-t', '--', 'me@work.example.com']
    assert argv[4] == remote.remote_string(m, ['resume', '--', 'claude:my name'])
    assert "'claude:my name'" in argv[4]
    with pytest.raises(ValueError):
        launch.remote_argv(cfg.Machine('w', '-oProxy=x', 'llmpro', ''), 'a')
    with pytest.raises(ValueError):
        launch.remote_argv(cfg.Machine('w', '', '', 'broken'), 'a')


def test_resume_at_machine_runs_ssh_and_reads_nothing_here(ssh, store, monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError('read on this machine')
    monkeypatch.setattr(sessions, 'local_rows', boom)
    monkeypatch.setattr(sessions, 'all_rows', boom)
    monkeypatch.setenv('FAKE_SSH_EXIT', '5')
    code, out, err = run(['resume', '--store', store, 'my name@work'], capsys)
    assert code == 5
    (call,) = ssh_calls(ssh)
    assert call[0] == '-t' and call[1] == '--' and call[2] == 'me@work.example.com'
    assert call[3].endswith("resume -- 'my name'") and 'llmpro' in call[3]
    assert '@work' not in call[3]


def test_resume_at_unknown_machine(ssh, store, capsys):
    code, out, err = run(['resume', '--store', store, 'x@nowhere'], capsys)
    assert code == 2 and 'nowhere' in err
    assert not [c for c in ssh_calls(ssh) if '-t' in c]  # the listing may ask; nothing is continued


def test_resume_remote_row_not_open_goes_through_ssh(ssh, store, monkeypatch, capsys):
    r = row(machine='work', name='far', folder='/remote/folder')
    monkeypatch.setattr(sessions, 'all_rows', lambda **kw: ([r], []))
    code, out, err = run(['resume', '--store', store, 'far'], capsys)
    assert code == 0
    (call,) = ssh_calls(ssh)
    assert call[1] == '--' and f'claude-code:{AID}' in call[3]


def test_resume_remote_row_open_goes_through_ssh(ssh, store, monkeypatch, capsys):
    """Package 20: the remote llmpro does the takeover there; nothing is closed from here."""
    r = row(machine='work', name='far', pid=88, where='ssh')
    monkeypatch.setattr(sessions, 'all_rows', lambda **kw: ([r], []))
    code, out, err = run(['resume', '--store', store, 'far'], capsys)
    assert code == 0
    (call,) = ssh_calls(ssh)
    assert call[1] == '--' and f'claude-code:{AID}' in call[3]


def test_resume_remote_dry_run(ssh, store, capsys):
    code, out, err = run(['resume', '--store', store, '-n', 'far@work'], capsys)
    assert code == 0 and ssh_calls(ssh) == []
    assert 'work' in out and 'ssh -t --' in out


def test_no_em_dash_in_the_new_files():
    for name in ('launch.py', 'seen.py', '__init__.py', 'remote.py'):
        with open(os.path.join(os.path.dirname(launch.__file__), name), encoding='utf-8') as fh:
            assert chr(0x2014) not in fh.read(), name
