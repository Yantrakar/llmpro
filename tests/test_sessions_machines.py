"""Package 16: `llmpro resume` asks the machines in [machines] over ssh. A fake `ssh` in tools_dir records its
argv and answers per target; nothing here runs a real ssh or reads the real home."""
import json
import os
import sys
import time

import pytest

from llmpro import cli, mcp, sessions, setup
from llmpro import store as cfg
from llmpro.sessions import remote

FAKE_SSH = '''#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
with open(os.path.join(here, 'ssh.log'), 'a') as fh:
    fh.write(json.dumps(argv) + '\\n')
target = argv[argv.index('--') + 1] if '--' in argv else ''
answer = json.load(open(os.path.join(here, 'ssh.json'))).get(target, {{}})
time.sleep(answer.get('sleep', 0))
sys.stderr.write(answer.get('stderr', ''))
sys.stdout.write(answer.get('stdout', ''))
sys.exit(answer.get('exit', 0))
'''


def row(**over):
    d = {'tool': 'claude-code', 'machine': 'whatever', 'id': 'aaaa-1111', 'name': 'demo', 'folder': '/home/u/code/x',
         'state': 'needs-reply', 'since': 1000, 'line': '', 'pid': 42, 'where': 'terminal', 'version': '2.0',
         'local_only': False, 'error': ''}
    d.update(over)
    return d


def good(*rows):
    return {'stdout': json.dumps({'ok': True, 'command': 'resume', 'format': sessions.FORMAT, 'machine': 'x',
                                  'rows': list(rows), 'errors': []}), 'exit': 0}


class Ssh:
    def __init__(self, folder):
        self.folder = folder
        self.answers = {}
        (folder / 'ssh').write_text(FAKE_SSH.format(python=sys.executable))
        (folder / 'ssh').chmod(0o755)
        self.save()

    def save(self):
        (self.folder / 'ssh.json').write_text(json.dumps(self.answers))

    def answer(self, target, **kw):
        self.answers[target] = kw
        self.save()

    def calls(self):
        log = self.folder / 'ssh.log'
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def ssh(tools_dir):
    return Ssh(tools_dir)


@pytest.fixture(autouse=True)
def fresh():
    sessions.begin()
    yield
    cfg.STORE = ''


def config(store, text):
    with open(f'{store}/config.toml', 'a', encoding='utf-8') as fh:
        fh.write('\n' + text)


def mach(name='work', ssh='me@work.example.com', llmpro='~/.local/bin/llmpro', error=''):
    return cfg.Machine(name, ssh, llmpro, error)


def run(args, capsys):
    code = cli.main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


# --- store.machines ---

def test_machines_forms_and_defaults(store):
    config(store, '[machines]\nlaptop = "laptop"\nwork = { ssh = "me@work.example.com", llmpro = "/opt/llmpro" }\n'
                  'plain = { ssh = "p.example.com" }\n')
    got = cfg.machines(store)
    assert got == [cfg.Machine('laptop', 'laptop', '~/.local/bin/llmpro', ''),
                   cfg.Machine('work', 'me@work.example.com', '/opt/llmpro', ''),
                   cfg.Machine('plain', 'p.example.com', '~/.local/bin/llmpro', '')]


def test_machines_skips_this_machine_by_name_and_hostname(store, monkeypatch):
    monkeypatch.setattr(cfg, 'MACHINE', 'here')
    config(store, '[machines]\nhere = "here.example.com"\nother = { ssh = "o.example.com", hostname = "here" }\n'
                  'work = "w.example.com"\n')
    assert [m.name for m in cfg.machines(store)] == ['work']


def test_machines_bad_entries_become_errors(store):
    config(store, '[machines]\ngood = "g.example.com"\nevil = "-oProxyCommand=x"\n"a b" = "c.example.com"\n'
                  'num = 5\nsemi = "h;rm"\nnossh = { llmpro = "x" }\nbadpath = { ssh = "h", llmpro = 3 }\n')
    got = {m.name: m for m in cfg.machines(store)}
    assert got['good'].error == ''
    for name in ('evil', 'a b', 'num', 'semi', 'nossh', 'badpath'):
        assert got[name].error.startswith(f'[machines] {name}: '), name
        assert got[name].ssh == ''


def test_machines_unreadable_config_gives_empty(store):
    open(f'{store}/config.toml', 'w').write('[machines\n')
    assert cfg.machines(store) == []
    assert cfg.machines(store + '-nowhere') == []


def test_template_has_commented_examples():
    import tomllib
    from pathlib import Path
    text = (Path(cfg.__file__).parent / 'templates' / 'config.toml').read_text()
    assert '# laptop = "laptop"' in text
    assert '# work = { ssh = "me@work.example.com", llmpro = "~/.local/bin/llmpro", hostname = "work-pc" }' in text
    assert tomllib.loads(text).get('machines', {}) == {}


# --- the command ---

def test_remote_string_quotes_every_word():
    assert remote.remote_string(mach(), ['resume', '--local']) == '"$HOME"/.local/bin/llmpro resume --local'
    assert remote.remote_string(mach(llmpro='$HOME/my bin/llmpro'), []) == '"$HOME"/\'my bin/llmpro\''
    assert remote.remote_string(mach(llmpro='/opt/a b/llmpro'), ['x; rm -rf ~', '$(id)']) == \
        "'/opt/a b/llmpro' 'x; rm -rf ~' '$(id)'"
    assert remote.remote_string(mach(llmpro='/o/llmpro;x'), []) == "'/o/llmpro;x'"


def test_command_argv():
    argv = remote.command(mach(), ['resume'])
    assert argv == ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '--', 'me@work.example.com',
                    '"$HOME"/.local/bin/llmpro resume']
    argv = remote.command(mach(), ['resume'], control='/tmp/c/x')
    assert argv[6:8] == ['-o', 'ControlPath=/tmp/c/x'] and argv.index('--') == 8


def test_command_refuses_a_target_with_a_dash_or_an_error():
    for m in (mach(ssh='-oProxyCommand=x'), mach(error='bad')):
        with pytest.raises(ValueError):
            remote.command(m, ['resume'])


def test_ask_good_rows_and_argv(ssh):
    ssh.answer('me@work.example.com', **good(row(line='secret words'), row(id='bbbb-2222', machine='other')))
    rows, error = remote.ask(mach())
    assert error == '' and [r.id for r in rows] == ['aaaa-1111', 'bbbb-2222']
    assert {r.machine for r in rows} == {'work'} and {r.line for r in rows} == {''}
    [argv] = ssh.calls()
    assert argv == ['-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '--', 'me@work.example.com',
                    '"$HOME"/.local/bin/llmpro resume --local --json --no-line']


def test_ask_passes_the_control_path(ssh):
    ssh.answer('h.example.com', **good())
    remote.ask(mach(ssh='h.example.com'), control='/tmp/c/x')
    assert 'ControlPath=/tmp/c/x' in ssh.calls()[0]


def test_ask_drops_bad_rows_and_cleans_strings(ssh):
    ssh.answer('h.example.com', **good(row(name='ev\x1b[31mil\x07'), row(state='bogus'), row(id='a b'),
                                       {'tool': 'x'}, 'text', row(pid='1')))
    rows, error = remote.ask(mach(ssh='h.example.com'))
    assert [r.name for r in rows] == ['evil'] and error == 'work: 5 of 6 rows could not be read'


@pytest.mark.parametrize('answer, text', [
    (dict(exit=255, stderr='me@h: Permission denied (publickey).'),
     'work: ssh could not log in without a password; set up a key, or run ssh me@work.example.com once'),
    (dict(exit=255, stderr='Could not resolve hostname'), 'work: cannot reach me@work.example.com'),
    (dict(exit=127, stderr='sh: 1: x: not found'),
     'work: llmpro not found at ~/.local/bin/llmpro; set llmpro in [machines]'),
    (dict(exit=1, stderr='bash: llmpro: command not found'),
     'work: llmpro not found at ~/.local/bin/llmpro; set llmpro in [machines]'),
    (dict(exit=0, stdout='Welcome to the machine\n'),
     'work: its llmpro does not know resume --json; run pipx upgrade llmpro there'),
    (dict(exit=0, stdout=json.dumps({'ok': True, 'rows': []})),
     'work: its llmpro does not know resume --json; run pipx upgrade llmpro there'),
    (dict(exit=0, stdout=json.dumps({'format': 'llmpro-sessions.v2', 'rows': []})),
     'work: its llmpro does not know resume --json; run pipx upgrade llmpro there'),
    (dict(exit=2, stderr='live sessions need Linux\n'), 'work: live sessions need Linux'),
    (dict(exit=2, stderr='usage: llmpro resume\nunrecognized arguments: --json'),
     'work: its llmpro does not know resume --json; run pipx upgrade llmpro there'),
])
def test_ask_errors(ssh, answer, text):
    ssh.answer('me@work.example.com', **answer)
    assert remote.ask(mach()) == ([], text)


def test_ask_timeout(ssh):
    ssh.answer('me@work.example.com', sleep=5, **good())
    t = time.monotonic()
    assert remote.ask(mach(), timeout=1) == ([], 'work: no answer within 1 seconds')
    assert time.monotonic() - t < 3


def test_ask_without_ssh_installed(tools_dir):
    assert remote.ask(mach())[1] == 'work: ssh is not installed here'


def test_ask_does_not_run_a_machine_with_an_error(ssh):
    assert remote.ask(mach(ssh='', error='[machines] work: bad')) == ([], '[machines] work: bad')
    assert ssh.calls() == []


# --- all_rows ---

@pytest.fixture
def local(monkeypatch):
    mine = sessions.Row('claude-code', cfg.MACHINE, 'cccc-3333', 'mine', '/x', 'working', 5, '', 1, 'terminal', '1',
                        False)
    monkeypatch.setattr(sessions, 'local_rows', lambda line=True, machine=None: [mine])
    return mine


def test_all_rows_merges_sorts_and_reports(store, ssh, local):
    config(store, '[machines]\nwork = "me@work.example.com"\nlap = "lap.example.com"\nevil = "-oProxyCommand=x"\n')
    ssh.answer('me@work.example.com', **good(row(state='needs-approval')))
    ssh.answer('lap.example.com', exit=255, stderr='no route')
    cfg.configure(store, required=False)
    rows, errors = sessions.all_rows()
    assert [(r.machine, r.state) for r in rows] == [('work', 'needs-approval'), (cfg.MACHINE, 'working')]
    assert {e['machine'] for e in errors} == {'lap', 'evil'}
    assert all(set(e) == {'machine', 'error'} for e in errors)
    assert len(ssh.calls()) == 2  # the bad entry was not asked


def test_all_rows_one_machine_and_own_name(store, ssh, local):
    config(store, '[machines]\nwork = "me@work.example.com"\nlap = "lap.example.com"\n')
    ssh.answer('me@work.example.com', **good(row()))
    cfg.configure(store, required=False)
    rows, errors = sessions.all_rows(machine='work')
    assert [r.machine for r in rows] == ['work'] and errors == []
    rows, errors = sessions.all_rows(machine=cfg.MACHINE)
    assert [r.id for r in rows] == ['cccc-3333'] and len(ssh.calls()) == 1
    rows, errors = sessions.all_rows(machine='nope')
    assert rows == [] and errors[0]['machine'] == 'nope'


def test_all_rows_asks_in_parallel(store, ssh, local):
    config(store, '[machines]\na = "a.example.com"\nb = "b.example.com"\n')
    for t in ('a.example.com', 'b.example.com'):
        ssh.answer(t, sleep=3, **good())
    cfg.configure(store, required=False)
    t = time.monotonic()
    rows, errors = sessions.all_rows(timeout=1)
    took = time.monotonic() - t
    assert took < 1.8 and sorted(e['error'] for e in errors) == ['a: no answer within 1 seconds',
                                                                  'b: no answer within 1 seconds']


def test_all_rows_passes_control_paths(store, ssh, local):
    config(store, '[machines]\nwork = "me@work.example.com"\n')
    ssh.answer('me@work.example.com', **good())
    cfg.configure(store, required=False)
    sessions.all_rows(control={'work': '/tmp/c/w'})
    assert 'ControlPath=/tmp/c/w' in ssh.calls()[0]


# --- llmpro resume ---

def test_resume_json_has_remote_rows_and_errors(store, ssh, local, capsys):
    config(store, '[machines]\nwork = "me@work.example.com"\nlap = "lap.example.com"\n')
    ssh.answer('me@work.example.com', **good(row(line='x')))
    ssh.answer('lap.example.com', exit=127)
    code, out, err = run(['resume', '--json', '--store', store], capsys)
    data = json.loads(out)
    assert code == 0 and data['ok'] is True
    assert sorted(r['machine'] for r in data['rows']) == sorted(['work', cfg.MACHINE])
    assert all(r['line'] == '' for r in data['rows'] if r['machine'] == 'work')
    assert data['errors'] == [{'machine': 'lap', 'error': 'lap: llmpro not found at ~/.local/bin/llmpro; '
                                                           'set llmpro in [machines]'}]


def test_resume_local_asks_nobody(store, ssh, local, capsys):
    config(store, '[machines]\nwork = "me@work.example.com"\n')
    code, out, _ = run(['resume', '--json', '--local', '--store', store], capsys)
    assert code == 0 and json.loads(out)['errors'] == [] and ssh.calls() == []


def test_resume_machine_option(store, ssh, local, capsys):
    config(store, '[machines]\nwork = "me@work.example.com"\n')
    ssh.answer('me@work.example.com', **good(row()))
    code, out, _ = run(['resume', '--json', '--machine', 'work', '--store', store], capsys)
    assert [r['machine'] for r in json.loads(out)['rows']] == ['work']


def test_resume_human_shows_error_rows_and_cleans(store, ssh, local, capsys, monkeypatch):
    config(store, '[machines]\nwork = "me@work.example.com"\nlap = "lap.example.com"\n')
    ssh.answer('me@work.example.com', **good(row(name='a\x1b[2Jb')))
    ssh.answer('lap.example.com', exit=255)
    code, out, err = run(['resume', '--store', store], capsys)
    assert code == 0 and '\x1b' not in out and 'ab@work' in out and 'lap: cannot reach lap.example.com' in out
    assert 'asking' not in err  # not a terminal


def test_resume_says_asking_on_a_terminal(store, ssh, local, capsys, monkeypatch):
    config(store, '[machines]\nwork = "me@work.example.com"\nlap = "lap.example.com"\nbad = "-x"\n')
    ssh.answer('me@work.example.com', **good())
    ssh.answer('lap.example.com', **good())
    monkeypatch.setattr(type(sys.stderr), 'isatty', lambda self: True, raising=False)
    code, out, err = run(['resume', '-n', '--store', store], capsys)
    assert code == 0 and err.count('asking 2 machines...') == 1 and err.index('asking') >= 0


def test_resume_bad_entries_do_not_break_other_commands(store, capsys):
    config(store, '[machines]\nevil = "-oProxyCommand=x"\n"a b" = "c"\nnum = 5\n')
    code, out, err = run(['sync', '-n', '--store', store], capsys)
    assert code in (0, 1) and 'machines' not in err


# --- setup -n ---

def test_setup_dry_run_reports_machines(store, ssh, machine, capsys):
    base = run(['setup', '-n', '--store', store], capsys)[0]
    config(store, '[machines]\nwork = "me@work.example.com"\nlap = "lap.example.com"\nevil = "-x"\n')
    ssh.answer('me@work.example.com', **good(row(), row(id='bbbb-2222')))
    ssh.answer('lap.example.com', exit=255, stderr='Permission denied')
    code, out, _ = run(['setup', '-n', '--store', store], capsys)
    assert code == base
    assert 'work: ok (2 open)' in out and 'lap: ssh could not log in without a password' in out
    assert '[machines] evil: ' in out
    code, out, _ = run(['setup', '-n', '--json', '--store', store], capsys)
    item = [i for i in json.loads(out)['items'] if i['name'] == 'machines'][0]
    assert item['present'] is True and 'machines' not in json.loads(out)['missing']


def test_setup_without_machines_has_no_machines_item(store, machine, capsys):
    assert 'machines' not in run(['setup', '-n', '--store', store], capsys)[1].split('harness')[0]


def test_setup_apply_does_nothing_for_machines():
    item = setup.Item('machines', False, 'present', ['x'])
    assert item.todo == [] and item.owner is None


# --- MCP ---

def result(body):
    return {'content': [{'type': 'text', 'text': body if isinstance(body, str) else json.dumps(body)}],
            'isError': False}


def test_gate_sessions_drops_lines_and_local_only_rows():
    body = {'ok': True, 'rows': [row(line='secret'), row(id='b-2', local_only=True, line='x'), row(id='c-3',
            local_only='no'), {'no': 'flag'}, 'junk', row(id='d-4', machine='work', line='more')], 'errors': []}
    out = mcp.gate_sessions(result(body))
    got = json.loads(out['content'][0]['text'])
    assert out['isError'] is False and [r['id'] for r in got['rows']] == ['aaaa-1111', 'd-4']
    assert all(r['line'] == '' for r in got['rows']) and got['withheld'] == 4


@pytest.mark.parametrize('text', ['not json', '[]', '{"rows": 3}', '{}', '{"rows": null}'])
def test_gate_sessions_refuses_what_it_cannot_check(text):
    out = mcp.gate_sessions(result(text))
    assert out['isError'] is True and 'could not be checked' in out['content'][0]['text']


def test_mcp_lists_the_sessions_tool_read_only():
    [tool] = [t for t in mcp.TOOLS if t['name'] == 'sessions']
    assert tool['annotations']['readOnlyHint'] is True and tool['inputSchema']['properties'] == {}
    assert 'read-only' in tool['description'].lower() and 'llmpro resume' in tool['description']
    assert 'sessions' in mcp.OWN
    assert mcp.command('sessions', {}) == ['resume', '--json', '--no-line']


def test_mcp_sessions_call_end_to_end(store, home, ssh, local, monkeypatch):
    config(store, '[machines]\nwork = "me@work.example.com"\n')
    ssh.answer('me@work.example.com', **good(row(local_only=True), row(id='b-2', line='l')))
    monkeypatch.setenv('PYTHONPATH', os.path.dirname(os.path.dirname(os.path.dirname(sessions.__file__))))
    out = mcp.call(store, 'sessions', {})
    if out['isError']:  # this machine may not support live sessions: then the child says so
        assert 'live sessions need Linux' in out['content'][0]['text']
        return
    rows = json.loads(out['content'][0]['text'])['rows']
    assert all(r['line'] == '' and r['local_only'] is False for r in rows)
    assert [r['id'] for r in rows if r['machine'] == 'work'] == ['b-2']
