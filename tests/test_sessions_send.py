"""Package 19: `llmpro send` gives one message to a session. Every tool is a small script in tools_dir that
records its argv, folder, stdin and the NAMES of its environment variables (and the value of
LLMPRO_SEND_HOPS only), then prints an answer in the tool's JSON form with made-up ids. Nothing here starts a
real claude, copilot, opencode or ssh, or reads the real home."""
import io
import json
import os
import subprocess
import sys
import time

import pytest

from llmpro import cli, sessions
from llmpro import store as cfg
from llmpro.sessions import launch, seen, send

AID = 'aaaaaaaa-1111-4111-8111-111111111111'
NEW = 'dddddddd-4444-4444-8444-444444444444'
OID = 'ses_0000000000aabbcc'

FAKE_TOOL = '''#!{python}
import json, os, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(here, '{name}.json')
conf = json.load(open(path)) if os.path.exists(path) else {{}}
argv = sys.argv[1:]
api = argv[:1] == ['api']
text = '' if api else sys.stdin.read()
with open(os.path.join(here, '{name}.log'), 'a') as fh:
    fh.write(json.dumps({{'argv': argv, 'cwd': os.getcwd(), 'stdin': text, 'env': sorted(os.environ),
                         'hops': os.environ.get('LLMPRO_SEND_HOPS')}}) + '\\n')
if api:
    print(conf.get('api', '{{}}'))
    sys.exit(0)
if conf.get('hang'):
    child = subprocess.Popen(['sleep', '60'])
    open(os.path.join(here, 'child.pid'), 'w').write(str(child.pid))
    time.sleep(60)
sys.stderr.write(conf.get('stderr', ''))
print(conf.get('stdout', ''))
sys.exit(conf.get('exit', 0))
'''

FAKE_SSH = '''#!{python}
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, 'ssh.log'), 'a') as fh:
    fh.write(json.dumps({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}) + '\\n')
sys.stdout.write(os.environ.get('FAKE_SSH_OUT', ''))
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
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def tools(tools_dir):
    return Tools(tools_dir)


@pytest.fixture(autouse=True)
def state(home, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    for name in list(os.environ):
        if name.startswith(('VSCODE_', 'CLAUDE', 'COPILOT_', 'OPENCODE', 'LLMPRO_SEND')) or name in ('TERM_PROGRAM', 'AI_AGENT'):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(send, 'GRACE', 1)
    sessions.begin()
    yield tmp_path / 'state'
    cfg.STORE = ''


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / 'proj'
    d.mkdir()
    return d


def row(**over):
    base = dict(tool='claude-code', machine=cfg.MACHINE, id=AID, name='demo', folder='/nonexistent', state='needs-reply',
                since=10, line='', pid=None, where='', version='2.1', local_only=False, error='')
    base.update(over)
    return sessions.Row(**base)


def listing(monkeypatch, *rows):
    monkeypatch.setattr(sessions, 'local_rows', lambda line=True, machine=None: list(rows))


def go(args, capsys):
    code = cli.main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


def config(store, text):
    with open(f'{store}/config.toml', 'a', encoding='utf-8') as fh:
        fh.write('\n' + text)


CLAUDE_OUT = json.dumps({'type': 'result', 'is_error': False, 'result': 'the answer', 'session_id': NEW,
                         'modelUsage': {'claude-haiku-5-5': {}}})


def copilot_out(model='m-1', content='hello there', session=NEW, result=True):
    lines = [{'type': 'session.start', 'data': {}},
             {'type': 'assistant.message', 'data': {'content': 'first', 'model': 'old'}},
             {'type': 'assistant.message', 'data': {'content': content, 'model': model}}]
    if result:
        lines.append({'type': 'result', 'sessionId': session, 'exitCode': 0})
    return '\n'.join(json.dumps(x) for x in lines)


def opencode_out(session=OID):
    lines = [{'type': 'step_start', 'sessionID': session, 'part': {}},
             {'type': 'text', 'sessionID': session, 'part': {'text': 'part one, '}},
             {'type': 'tool_use', 'sessionID': session, 'part': {}},
             {'type': 'text', 'sessionID': session, 'part': {'text': 'part two'}},
             {'type': 'step_finish', 'sessionID': session, 'part': {}}]
    return '\n'.join(json.dumps(x) for x in lines)


def piped(monkeypatch, text):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(text))


BANNED = ('allow', 'yolo', 'dangerously', 'bypass', '--permission')


def clean_argv(argv):
    assert not [a for a in argv if any(b in a.lower() for b in BANNED)], argv


# ---------------------------------------------------------------- channels

def test_claude_channel_text_on_stdin(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    code, out, err = go(['send', '--store', store, 'demo', 'please do the thing'], capsys)
    assert code == 0 and out == 'the answer\n'
    (call,) = tools.calls('claude')
    assert call['argv'] == ['-p', '--output-format', 'json', '--resume', AID]
    assert call['stdin'] == 'please do the thing' and call['cwd'] == str(folder)
    assert 'please' not in ' '.join(call['argv'])
    assert call['hops'] == '1'


def test_copilot_channel_has_no_p(tools, folder, store, monkeypatch, capsys):
    tools.make('copilot', stdout=copilot_out())
    listing(monkeypatch, row(tool='copilot-cli', folder=str(folder)))
    code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
    assert code == 0 and out == 'hello there\n'
    (call,) = tools.calls('copilot')
    assert call['argv'] == ['-s', '--output-format', 'json', '--resume', AID] and call['stdin'] == 'hi'


def test_opencode_channel_works_open_or_not(tools, folder, store, monkeypatch, capsys):
    tools.make('opencode', stdout=opencode_out())
    listing(monkeypatch, row(tool='opencode', id=OID, folder=str(folder), pid=77, where='terminal'))
    code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
    assert code == 0 and out == 'part one, part two\n'
    assert tools.calls('opencode')[0]['argv'] == ['run', '--format', 'json', '-s', OID]


def test_text_that_looks_like_a_flag_stays_text(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    piped(monkeypatch, '--dangerously-skip-permissions --yolo')
    code, out, err = go(['send', '--store', store, 'demo', '-'], capsys)
    assert code == 0
    (call,) = tools.calls('claude')
    clean_argv(call['argv'])
    assert call['stdin'] == '--dangerously-skip-permissions --yolo'
    with pytest.raises(SystemExit):
        cli.run(['send', '--store', store, 'demo', '--yolo'])  # argparse: not an option, and never run as text
    capsys.readouterr()
    assert len(tools.calls('claude')) == 1


def test_text_from_stdin_when_missing(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    piped(monkeypatch, 'from a pipe')
    assert go(['send', '--store', store, 'demo'], capsys)[0] == 0
    assert tools.calls('claude')[0]['stdin'] == 'from a pipe'


def test_text_too_long_or_empty(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    piped(monkeypatch, 'x' * (64 * 1024 + 1))
    assert go(['send', '--store', store, 'demo', '-'], capsys)[0] == 2
    piped(monkeypatch, '   ')
    assert go(['send', '--store', store, 'demo', '-'], capsys)[0] == 2
    assert tools.calls('claude') == []


def test_open_session_is_refused(tools, folder, store, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch, row(folder=str(folder), pid=9, where='terminal'))
    code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
    assert code == 1 and 'is open in terminal on' in err and 'llmpro does not type into an open session' in err
    assert tools.calls('claude') == []


def test_container_row_is_refused_even_for_opencode(tools, folder, store, monkeypatch, capsys):
    tools.make('opencode')
    listing(monkeypatch, row(tool='opencode', id=OID, folder=str(folder), pid=9, where='container'))
    code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
    assert code == 1 and 'container' in err and tools.calls('opencode') == []


def test_model_is_the_only_flag_passed_and_must_look_like_a_name(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    tools.make('opencode', stdout=opencode_out())
    listing(monkeypatch, row(folder=str(folder)), row(tool='opencode', id=OID, folder=str(folder), name='oc'))
    go(['send', '--store', store, 'demo', 'hi', '--model', 'haiku'], capsys)
    assert tools.calls('claude')[0]['argv'][-2:] == ['--model', 'haiku']
    go(['send', '--store', store, 'oc', 'hi', '--model', 'p/m'], capsys)
    assert tools.calls('opencode')[0]['argv'][-2:] == ['-m', 'p/m']
    code, out, err = go(['send', '--store', store, 'demo', 'hi', '--model=--yolo'], capsys)
    assert code == 2 and len(tools.calls('claude')) == 1


def test_no_permission_flag_in_any_channel(tools, folder, store, monkeypatch, capsys):
    for name, out in (('claude', CLAUDE_OUT), ('copilot', copilot_out()), ('opencode', opencode_out())):
        tools.make(name, stdout=out)
    listing(monkeypatch, row(folder=str(folder), name='a'), row(tool='copilot-cli', folder=str(folder), name='b'),
            row(tool='opencode', id=OID, folder=str(folder), name='c'))
    for n in 'abc':
        go(['send', '--store', store, n, 'hi'], capsys)
    for name in ('claude', 'copilot', 'opencode'):
        clean_argv(tools.calls(name)[0]['argv'])


def test_tool_missing(folder, store, monkeypatch, capsys):
    listing(monkeypatch, row(folder=str(folder)))
    code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
    assert code == 1 and 'claude is not installed' in err


def test_unsupported_system(store, monkeypatch, capsys):
    monkeypatch.setattr(sessions, 'supported', lambda: False)
    code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
    assert code == 2 and 'live sessions need Linux' in err


def test_unknown_target(tools, store, monkeypatch, capsys):
    listing(monkeypatch)
    code, out, err = go(['send', '--store', store, 'nothing-here', 'hi'], capsys)
    assert code == 2 and 'no session matches' in err


def test_environment_is_cleaned_and_hops_set(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    monkeypatch.setenv('VSCODE_IPC_HOOK', 'x')
    monkeypatch.setenv('LLMPRO_SEND_HOPS', '1')
    assert go(['send', '--store', store, 'demo', 'hi'], capsys)[0] == 0
    (call,) = tools.calls('claude')
    assert call['hops'] == '2' and 'VSCODE_IPC_HOOK' not in call['env']


def test_a_send_never_carries_a_widening_variable(tools, folder, store, monkeypatch, capsys):
    tools.make('copilot', stdout=copilot_out())
    listing(monkeypatch, row(tool='copilot-cli', folder=str(folder)))
    monkeypatch.setenv('COPILOT_ALLOW_ALL', 'true')
    monkeypatch.setenv('COPILOT_MODEL', 'm')  # a plain setting the user exports goes on
    assert go(['send', '--store', store, 'demo', 'hi'], capsys)[0] == 0
    (call,) = tools.calls('copilot')
    assert 'COPILOT_ALLOW_ALL' not in call['env'] and 'COPILOT_MODEL' in call['env']


# ---------------------------------------------------------------- limits

def test_hops_from_the_variable(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    monkeypatch.setenv('LLMPRO_SEND_HOPS', '2')
    code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
    assert code == 1 and 'this message would be hop 3; the limit is max_hops in [sessions]' in err
    code, out, err = go(['send', '--store', store, 'demo', 'hi', '--hops', '0'], capsys)
    assert code == 1 and tools.calls('claude') == []  # max(--hops, variable)


def test_hops_option_and_config_limit(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    assert go(['send', '--store', store, 'demo', 'hi', '--hops', '2'], capsys)[0] == 1
    assert go(['send', '--store', store, 'demo', 'hi', '--hops', '1'], capsys)[0] == 0
    assert tools.calls('claude')[0]['hops'] == '2'
    config(store, '[sessions]\nmax_hops = 5\n')
    assert go(['send', '--store', store, 'demo', 'hi', '--hops', '4'], capsys)[0] == 0


@pytest.mark.parametrize('how', ['hops', 'CLAUDECODE', 'inside', 'COPILOT_AGENT_SESSION_ID', 'OPENCODE'])
def test_local_only_session_refused_from_inside(how, tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder), local_only=True))
    args = ['send', '--store', store, 'demo', 'hi']
    if how == 'hops':
        monkeypatch.setenv('LLMPRO_SEND_HOPS', '0')
    elif how == 'inside':
        args.append('--inside')
    else:
        monkeypatch.setenv(how, '1')
    code, out, err = go(args, capsys)
    assert code == 1 and 'is in a local-only project; send to it from your own terminal' in err
    assert tools.calls('claude') == []


def test_local_only_session_allowed_outside_and_with_vscode_only(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder), local_only=True))
    monkeypatch.setenv('VSCODE_IPC_HOOK_CLI', 'x')
    monkeypatch.setenv('TERM_PROGRAM', 'vscode')
    assert go(['send', '--store', store, 'demo', 'hi'], capsys)[0] == 0
    assert len(tools.calls('claude')) == 1


def test_new_session_in_local_only_folder(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    with open(f'{store}/config.toml', 'w') as fh:
        fh.write(f'[store]\nformat = 1\nredact_version = 1\n[privacy]\nlocal_only = ["{folder}"]\n')
    args = ['send', '--store', store, 'claude:new', 'hi', '--folder', str(folder)]
    code, out, err = go(args + ['--inside'], capsys)
    assert code == 1 and 'local-only project' in err and tools.calls('claude') == []
    assert go(args, capsys)[0] == 0
    assert len(tools.calls('claude')) == 1


def test_remote_local_only_row_refused_from_inside(tools_dir, store, monkeypatch, capsys):
    (tools_dir / 'ssh').write_text(FAKE_SSH.format(python=sys.executable))
    (tools_dir / 'ssh').chmod(0o755)
    config(store, '[machines]\nwork = "me@work.example.com"\n')
    r = row(machine='work', name='far', local_only=True, folder='/remote/f')
    monkeypatch.setattr(sessions, 'all_rows', lambda **kw: ([r], []))
    code, out, err = go(['send', '--store', store, 'far', 'hi', '--inside'], capsys)
    assert code == 1 and 'local-only project' in err
    assert not (tools_dir / 'ssh.log').exists()


def test_rate_limit_across_calls(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    config(store, '[sessions]\nsends_per_hour = 2\n')
    assert go(['send', '--store', store, 'demo', 'hi'], capsys)[0] == 0
    assert go(['send', '--store', store, 'demo', 'hi'], capsys)[0] == 0
    code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
    assert code == 1 and 'sends_per_hour' in err and len(tools.calls('claude')) == 2
    data = json.loads(open(send.sends_path()).read())
    assert list(data) == [f'claude-code:{AID}'] and len(data[f'claude-code:{AID}']) == 2
    assert oct(os.stat(send.sends_path()).st_mode & 0o777) == '0o600'


def test_old_sends_do_not_count(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    config(store, '[sessions]\nsends_per_hour = 1\n')
    send.count(f'claude-code:{AID}', 1, now=time.time() - 7200)
    assert go(['send', '--store', store, 'demo', 'hi'], capsys)[0] == 0


def test_dry_run_counts_nothing_and_takes_no_lock(tools, folder, store, monkeypatch, capsys):
    tools.make('claude')
    listing(monkeypatch, row(folder=str(folder)))
    monkeypatch.setattr(launch, 'lock', lambda r: (_ for _ in ()).throw(AssertionError('lock')))
    code, out, err = go(['send', '-n', '--store', store, 'demo', 'hi'], capsys)
    assert code == 0 and '--resume' in out and 'claude' in out
    assert tools.calls('claude') == [] and not os.path.exists(send.sends_path())


def test_new_sessions_count_under_tool_new_folder(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    config(store, '[sessions]\nsends_per_hour = 1\n')
    args = ['send', '--store', store, 'claude:new', 'hi', '--folder', str(folder)]
    assert go(args, capsys)[0] == 0
    assert go(args, capsys)[0] == 1
    assert list(json.loads(open(send.sends_path()).read())) == [f'claude-code:new@{folder}']


HOLDER = '''
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print('held', flush=True)
sys.stdin.read()
'''


def test_lock_held_by_another_process(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    live = os.path.join(seen.state_dir(), 'live')
    os.makedirs(live)
    p = subprocess.Popen([sys.executable, '-c', HOLDER, os.path.join(live, f'claude-code-{AID}.lock')],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == 'held'
        code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
        assert code == 1 and 'busy with another llmpro' in err and tools.calls('claude') == []
    finally:
        p.stdin.close()
        p.wait()
    assert go(['send', '--store', store, 'demo', 'hi'], capsys)[0] == 0


def test_session_open_after_the_lock_is_refused(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    monkeypatch.setattr(launch, 'now_open', lambda x: row(folder=str(folder), pid=5, where='terminal'))
    code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
    assert code == 1 and 'is open in terminal' in err and tools.calls('claude') == []
    assert not os.path.exists(send.sends_path())


# ---------------------------------------------------------------- answers, models, ids

def test_model_warning_claude_alias_is_not_a_difference(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    code, out, err = go(['send', '--store', store, 'demo', 'hi', '--model', 'haiku'], capsys)
    assert code == 0 and 'warning' not in err
    code, out, err = go(['send', '--store', store, 'demo', 'hi', '--model', 'opus'], capsys)
    assert 'warning: asked for opus, claude-code used claude-haiku-5-5' in err


def test_model_warning_copilot_uses_the_message_model(tools, folder, store, monkeypatch, capsys):
    lines = [{'type': 'session.resume', 'data': {'selectedModel': 'old-model'}},
             {'type': 'assistant.message', 'data': {'content': 'ok', 'model': 'new-model'}},
             {'type': 'result', 'sessionId': AID, 'exitCode': 0}]
    tools.make('copilot', stdout='\n'.join(json.dumps(x) for x in lines))
    listing(monkeypatch, row(tool='copilot-cli', folder=str(folder)))
    code, out, err = go(['send', '--store', store, '--json', 'demo', 'hi', '--model', 'old-model'], capsys)
    body = json.loads(out)
    assert code == 0 and body['model'] == 'new-model'
    assert body['warnings'] == ['asked for old-model, copilot-cli used new-model']
    assert 'warning: asked for old-model' in err
    code, out, err = go(['send', '--store', store, 'demo', 'hi', '--model', 'new-model'], capsys)
    assert 'warning' not in err


def test_model_for_opencode_comes_from_session_get(tools, folder, store, monkeypatch, capsys):
    tools.make('opencode', stdout=opencode_out(), api=json.dumps({'model': {'providerID': 'p', 'modelID': 'other'}}))
    listing(monkeypatch, row(tool='opencode', id=OID, folder=str(folder)))
    code, out, err = go(['send', '--store', store, 'demo', 'hi', '--model', 'p/wanted'], capsys)
    assert code == 0 and 'asked for p/wanted, opencode used p/other' in err
    api = [c for c in tools.calls('opencode') if c['argv'][0] == 'api']
    assert api[0]['argv'] == ['api', 'session.get', '--param', f'sessionID={OID}']


def test_copilot_exit_zero_without_a_result_line_fails(tools, folder, store, monkeypatch, capsys):
    tools.make('copilot', stdout='Model "zzz" from --model flag is not available.')
    listing(monkeypatch, row(tool='copilot-cli', folder=str(folder)))
    code, out, err = go(['send', '--store', store, 'demo', 'hi', '--model', 'zzz'], capsys)
    assert code == 1 and 'no result' in err and out == ''


def test_tool_status_nonzero_is_exit_one_and_reported(tools, folder, store, monkeypatch, capfd):
    tools.make('claude', stdout=CLAUDE_OUT, exit=3, stderr='boom\n')
    listing(monkeypatch, row(folder=str(folder)))
    code = cli.main(['send', '--store', store, '--json', 'demo', 'hi'])
    out, err = capfd.readouterr()
    assert code == 1 and 'boom' in err and 'status 3' in err  # the tool's stderr passes through
    assert json.loads(out)['ok'] is False and json.loads(out)['exit'] == 3  # the tool's own status


def test_new_session_ids_from_each_tools_json(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    tools.make('copilot', stdout=copilot_out())
    tools.make('opencode', stdout=opencode_out(session=OID))
    for short, name, sid in (('claude', 'claude', NEW), ('copilot', 'copilot', NEW), ('opencode', 'opencode', OID)):
        code, out, err = go(['send', '--store', store, f'{short}:new', 'hi', '--folder', str(folder)], capsys)
        assert code == 0 and f':{sid}' in err, (short, err)
        argv = tools.calls(name)[0]['argv']
        assert '--resume' not in argv and sid not in argv and (name == 'copilot' or '-s' not in argv)
        assert tools.calls(name)[0]['cwd'] == str(folder)
    ids = {(e['tool'], e['id']) for e in seen.load()}
    assert ids == {('claude-code', NEW), ('copilot-cli', NEW), ('opencode', OID)}
    assert tools.calls('opencode')[0]['argv'] == ['run', '--format', 'json']


def test_new_session_without_an_id(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=json.dumps({'result': 'x'}))
    code, out, err = go(['send', '--store', store, 'claude:new', 'hi', '--folder', str(folder)], capsys)
    assert code == 0 and 'could not tell which session was created; find it with llmpro resume' in err
    assert seen.load() == []


def test_new_session_model_and_default_folder(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    monkeypatch.chdir(folder)
    go(['send', '--store', store, 'claude:new', 'hi', '--model', 'haiku'], capsys)
    (call,) = tools.calls('claude')
    assert call['argv'] == ['-p', '--output-format', 'json', '--model', 'haiku']
    assert os.path.realpath(call['cwd']) == os.path.realpath(folder)


def test_answer_keeps_its_lines_but_loses_control_characters(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=json.dumps({'result': 'line one\nline \x1b[31mtwo\x1b[0m\x07', 'session_id': NEW}))
    listing(monkeypatch, row(folder=str(folder)))
    code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
    assert out == 'line one\nline two\n'


def test_json_envelope(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', stdout=CLAUDE_OUT)
    listing(monkeypatch, row(folder=str(folder)))
    code, out, err = go(['send', '--store', store, '--json', 'demo', 'hi'], capsys)
    body = json.loads(out)
    assert code == 0 and body['ok'] is True and body['command'] == 'send'
    assert body['target'] == 'demo' and body['machine'] == cfg.MACHINE and body['answer'] == 'the answer'
    assert body['exit'] == 0 and body['warnings'] == [] and body['model'] == 'claude-haiku-5-5'


def test_json_refusal(tools, folder, store, monkeypatch, capsys):
    listing(monkeypatch, row(folder=str(folder), pid=3, where='terminal'))
    code, out, err = go(['send', '--store', store, '--json', 'demo', 'hi'], capsys)
    body = json.loads(out)
    assert code == 1 and body['ok'] is False and 'open' in body['error']


# ---------------------------------------------------------------- timeout

def alive(pid):
    try:
        os.kill(pid, 0)
        with open(f'/proc/{pid}/stat') as fh:
            return fh.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except OSError:
        return False


def test_timeout_ends_the_whole_group(tools, folder, store, monkeypatch, capsys):
    tools.make('claude', hang=True)
    listing(monkeypatch, row(folder=str(folder)))
    config(store, '[sessions]\nsend_timeout = 1\n')
    started = time.time()
    code, out, err = go(['send', '--store', store, 'demo', 'hi'], capsys)
    assert code == 1 and 'did not finish within 1 seconds' in err and time.time() - started < 20
    pid = int((tools.folder / 'child.pid').read_text())
    for _ in range(50):
        if not alive(pid):
            break
        time.sleep(0.1)
    assert not alive(pid)


def test_spawn_starts_a_new_session():
    status, out, timed = send.spawn([sys.executable, '-c', 'import os; print(os.getpgrp() == os.getpid())'], b'')
    assert status == 0 and out.strip() == 'True' and not timed


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


def test_remote_send_through_ssh_with_text_on_stdin(ssh, store, monkeypatch, capsys):
    monkeypatch.setenv('FAKE_SSH_OUT', 'remote answer\n')
    monkeypatch.setenv('FAKE_SSH_EXIT', '1')
    code, out, err = go(['send', '--store', store, 'my name@work', 'it is $(done); rm', '--model', 'a b',
                         '--folder', '/srv/x y', '-n', '--json', '--inside', '--hops', '1'], capsys)
    assert code == 1 and out == 'remote answer\n'
    (call,) = ssh_calls(ssh)
    argv = call['argv']
    assert argv[:3] == ['-T', '-o', 'BatchMode=yes'] and argv[-2] == 'me@work.example.com' and argv[-3] == '--'
    remote_cmd = argv[-1]
    assert remote_cmd.endswith("send 'my name' - --hops 1 --inside --folder '/srv/x y' --model 'a b' -n --json")
    assert 'rm' not in remote_cmd and call['stdin'] == 'it is $(done); rm'
    assert '@work' not in remote_cmd


def test_remote_row_goes_by_tool_and_id(ssh, store, monkeypatch, capsys):
    r = row(machine='work', name='far', folder='/remote/f')  # no claude here: the other machine runs it
    monkeypatch.setattr(sessions, 'all_rows', lambda **kw: ([r], []))
    code, out, err = go(['send', '--store', store, 'far', 'hi'], capsys)
    assert code == 0
    (call,) = ssh_calls(ssh)
    assert f'send claude-code:{AID} - --hops 0' in call['argv'][-1] and '--inside' not in call['argv'][-1]
    assert call['stdin'] == 'hi'


def test_remote_open_row_is_refused_before_ssh(ssh, store, monkeypatch, capsys):
    r = row(machine='work', name='far', pid=4, where='ssh')
    monkeypatch.setattr(sessions, 'all_rows', lambda **kw: ([r], []))
    code, out, err = go(['send', '--store', store, 'far', 'hi'], capsys)
    assert code == 1 and 'is open in ssh on work' in err and ssh_calls(ssh) == []


def test_remote_new_needs_a_folder(ssh, store, capsys):
    code, out, err = go(['send', '--store', store, 'claude:new@work', 'hi'], capsys)
    assert code == 2 and 'needs --folder' in err and ssh_calls(ssh) == []
    code, out, err = go(['send', '--store', store, 'claude:new@work', 'hi', '--folder', '/srv/p'], capsys)
    assert code == 0 and 'send claude:new - --hops 0 --folder /srv/p' in ssh_calls(ssh)[0]['argv'][-1]


def test_unknown_machine_is_not_a_machine(ssh, store, monkeypatch, capsys):
    monkeypatch.setattr(sessions, 'all_rows', lambda **kw: ([], []))
    code, out, err = go(['send', '--store', store, 'x@nowhere', 'hi'], capsys)
    assert code == 2 and 'nowhere' in err and ssh_calls(ssh) == []


# ---------------------------------------------------------------- files

def test_capabilities():
    assert {'send', 'send.json'} <= set(cli.CAPABILITIES) and list(cli.CAPABILITIES) == sorted(cli.CAPABILITIES)


def test_inside_constant_has_no_vscode():
    assert not [v for v in launch.INSIDE if v.startswith('VSCODE')]
    assert {'CLAUDECODE', 'OPENCODE', 'COPILOT_CLI'} <= set(launch.INSIDE)


def test_docs_mention_send_and_no_em_dash():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ('README.md', 'skills/llmpro/SKILL.md'):
        text = open(os.path.join(root, rel), encoding='utf-8').read()
        assert 'llmpro send' in text and 'llmpro resume' in text and chr(0x2014) not in text, rel
    for name in ('send.py', 'launch.py'):
        with open(os.path.join(os.path.dirname(launch.__file__), name), encoding='utf-8') as fh:
            assert chr(0x2014) not in fh.read(), name
    with open(os.path.abspath(__file__), encoding='utf-8') as fh:
        assert chr(0x2014) not in fh.read()
