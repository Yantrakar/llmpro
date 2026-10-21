"""Package 18: `llmpro resume --watch` and the desktop notices. The clock, the key reader, the local rows and the
answers of the other machines are injected, so no test waits for real time, reads a terminal or starts a real
ssh, notify-send, claude, copilot or opencode. The ssh and notify-send programs here are scripts in tools_dir
that write their argv to a file."""
import io
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from llmpro import cli, notice, sessions, sync
from llmpro import store as cfg
from llmpro.sessions import launch, watch

ME = 'laptop'
EM_DASH = '\u2014'

FAKE_SSH = '''#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
target = argv[argv.index('--') + 1] if '--' in argv else ''
conf = json.load(open(os.path.join(here, 'ssh.json'))).get(target, {{}})
start = time.time()
if '-O' in argv:
    kind = 'exit'
elif '-fN' in argv:
    kind = 'login'
    time.sleep(conf.get('login_sleep', 0))
else:
    kind = 'ask'
with open(os.path.join(here, 'ssh.log'), 'a') as fh:
    fh.write(json.dumps({{'kind': kind, 'argv': argv, 'start': start, 'end': time.time()}}) + '\\n')
if kind == 'login':
    sys.exit(conf.get('login_exit', 0))
if kind == 'ask':
    sys.stdout.write(conf.get('stdout', ''))
sys.exit(0)
'''

FAKE_NOTIFY = '''#!{python}
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, 'notify.log'), 'a') as fh:
    fh.write(json.dumps(sys.argv[1:]) + '\\n')
'''


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

    def calls(self, kind=None):
        log = self.folder / 'ssh.log'
        every = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        return [c for c in every if kind is None or c['kind'] == kind]


class Notify:
    def __init__(self, folder):
        self.log = folder / 'notify.log'
        (folder / 'notify-send').write_text(FAKE_NOTIFY.format(python=sys.executable))
        (folder / 'notify-send').chmod(0o755)

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


@pytest.fixture
def ssh(tools_dir):
    return Ssh(tools_dir)


@pytest.fixture(autouse=True)
def fresh(home, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(home / 'state'))
    monkeypatch.setattr(cfg, 'MACHINE', ME)
    sessions.begin()
    yield
    cfg.STORE = ''


def row(id='aaaa-1111', state='needs-reply', machine=ME, **over):
    d = dict(tool='claude-code', machine=machine, id=id, name='demo', folder='/home/u/code/x', state=state, since=1000,
             line='', pid=None, where='terminal', version='2.0', local_only=False, error='')
    d.update(over)
    return sessions.Row(**d)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Keys:
    """Scripted key reader. Each item is a line, None (nothing typed: time passes), or an exception to raise.
    When the script is used up it ends the input."""
    def __init__(self, clock, *items):
        self.clock, self.items, self.timeouts = clock, list(items), []

    def __call__(self, timeout):
        self.timeouts.append(timeout)
        if not self.items:
            raise EOFError
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        if item is None:
            self.clock.t += timeout if timeout is not None else 0
        return item


class Source:
    """Rows for the local machine: one list per refresh; the last list is used again."""
    def __init__(self, *cycles):
        self.cycles, self.calls = list(cycles), 0

    def __call__(self, line=True):
        i = min(self.calls, len(self.cycles) - 1)
        self.calls += 1
        if isinstance(self.cycles[i], BaseException):
            raise self.cycles[i]
        return list(self.cycles[i])


def args(*extra):
    return cli.build_parser().parse_args(['resume', '--watch', *extra])


def write_config(store, text):
    with open(f'{store}/config.toml', 'a', encoding='utf-8') as fh:
        fh.write('\n' + text)


class Run:
    """watch.run with everything injected. `keys` are the items of the key script."""
    def __init__(self, store, *keys, local=(), extra=(), grace=0.05, ask=None, max_cycles=None):
        self.clock = Clock()
        self.keys = Keys(self.clock, *keys)
        self.out = io.StringIO()
        self.sent = []
        self.local = Source(*(local or ([],)))
        cfg.configure(store, required=False)
        self.code = watch.run(args(*extra), store, clock=self.clock, read=self.keys, sleep=self.sleep, out=self.out,
                              local=self.local, ask=ask, send=lambda title, msg: self.sent.append((title, msg)),
                              grace=grace, max_cycles=max_cycles)

    def sleep(self, secs):
        self.clock.t += secs

    @property
    def screens(self):
        return self.out.getvalue().split(watch.CLEAR)[1:]


def tick(n):
    return [None] * n


# --- notices ---

def test_notice_on_transitions_only_and_none_for_rows_at_the_start(store):
    first = [row('a1', 'working', name='alpha'), row('b1', 'needs-reply', name='beta')]
    second = [row('a1', 'needs-reply', name='alpha'), row('b1', 'needs-reply', name='beta'),
              row('c1', 'needs-approval', name='gamma')]
    third = [row('a1', 'needs-reply', name='alpha'), row('b1', 'working', name='beta'),
             row('c1', 'needs-approval', name='gamma')]
    r = Run(store, *tick(3), local=[first, second, third])
    assert r.sent == [('llmpro', 'claude-code alpha on laptop: needs a reply'),
                      ('llmpro', 'claude-code gamma on laptop: needs approval')]


def test_approval_to_reply_is_a_notice_and_working_to_unknown_is_not(store):
    first = [row('a1', 'needs-approval'), row('b1', 'working')]
    second = [row('a1', 'needs-reply'), row('b1', 'unknown')]
    r = Run(store, *tick(2), local=[first, second])
    assert r.sent == [('llmpro', 'claude-code demo on laptop: needs a reply')]


def test_notices_for_one_row_are_sixty_seconds_apart(store):
    waiting, busy = [row('a1', 'needs-reply')], [row('a1', 'working')]
    # a refresh every 5 seconds: waiting at 5, busy at 10, waiting again at 15 (too soon), later again
    cycles = [busy, waiting, busy, waiting, busy] + [busy] * 12 + [waiting]
    r = Run(store, *tick(len(cycles)), local=cycles)
    times = r.clock.t
    assert len(r.sent) == 2, (r.sent, times)


def test_gap_counts_from_the_notice_not_from_the_change(store):
    waiting, busy = [row('a1', 'needs-reply')], [row('a1', 'working')]
    cycles = [busy, waiting, busy, waiting]
    r = Run(store, *tick(4), local=cycles)
    assert len(r.sent) == 1
    r2 = Run(store, None, None, None, None, local=cycles)  # same again in a fresh run: a fresh gap
    assert len(r2.sent) == 1


def test_notify_false_sends_nothing(store):
    write_config(store, '[sessions]\nnotify = false\n')
    r = Run(store, *tick(2), local=[[row('a1', 'working')], [row('a1', 'needs-reply')]])
    assert r.sent == []


def test_local_only_row_leaves_out_name_and_folder(store):
    secret = row('a1', 'needs-reply', name='client-merger', folder='/home/u/code/client', local_only=True, line='the line')
    r = Run(store, *tick(2), local=[[row('a1', 'working', local_only=True)], [secret]])
    assert r.sent == [('llmpro', 'claude-code on laptop: needs a reply')]


def test_notice_never_has_the_line_and_is_cleaned(store):
    nasty = row('a1', 'needs-approval', name='bad\x1b[31mname\x07\nnext', machine='box\x1b]0;x\x07', line='SECRET LINE')
    r = Run(store, *tick(2), local=[[], [nasty]])
    (title, msg), = r.sent
    assert msg == 'claude-code badname next on box: needs approval'
    assert 'SECRET' not in msg and not any(ord(c) < 32 for c in msg)


def test_notice_names_the_id_when_there_is_no_name(store):
    r = Run(store, *tick(2), local=[[], [row('abcdef12-3456', 'needs-reply', name='')]])
    assert r.sent == [('llmpro', 'claude-code abcdef12 on laptop: needs a reply')]


def test_rows_with_a_failed_ask_are_not_new_when_they_come_back(store, monkeypatch):
    write_config(store, '[machines]\nwork = "me@work.example.com"\n')
    answers = iter([([row('w1', 'needs-reply', machine='work')], ''), ([], 'work: cannot reach me@work.example.com'),
                    ([row('w1', 'needs-reply', machine='work')], '')])
    r = Run(store, *tick(3), ask=lambda m, timeout=10, control=None: next(answers), grace=2)
    assert r.sent == []


def test_a_machine_that_first_answers_late_is_seeded_not_noticed(store):
    write_config(store, '[machines]\nwork = "me@work.example.com"\n')
    answers = iter([([], 'work: cannot reach me@work.example.com'),
                    ([row('w1', 'needs-reply', machine='work')], ''),
                    ([row('w1', 'needs-reply', machine='work')], '')])
    r = Run(store, *tick(3), ask=lambda m, timeout=10, control=None: next(answers), grace=2)
    assert r.sent == []


def test_notice_goes_through_notice_notify_and_notify_send(store, tools_dir, monkeypatch):
    fake = Notify(tools_dir)
    clock = Clock()
    out = io.StringIO()
    keys = Keys(clock, None, None)
    cfg.configure(store, required=False)
    watch.run(args(), store, clock=clock, read=keys, sleep=lambda s: None, out=out, grace=0.05,
              local=Source([row('a1', 'working')], [row('a1', 'needs-reply')]))
    assert fake.calls() == [['--app-name=llmpro', 'llmpro', 'claude-code demo on laptop: needs a reply']]


# --- notice.notify and sync.notify ---

def test_notice_notify_with_and_without_icon(tools_dir):
    notice.notify('t', 'm')  # no notify-send found: nothing happens
    fake = Notify(tools_dir)
    notice.notify('t', 'm')
    notice.notify('t2', 'm2', icon='dialog-warning')
    assert fake.calls() == [['--app-name=llmpro', 't', 'm'], ['--app-name=llmpro', '--icon=dialog-warning', 't2', 'm2']]


def test_sync_notify_keeps_its_title_and_icon(tools_dir):
    fake = Notify(tools_dir)
    sync.notify('boom')
    assert fake.calls() == [['--app-name=llmpro', '--icon=dialog-warning', 'llmpro sync failed', 'boom']]


# --- the screen ---

def test_screen_has_the_list_the_time_and_the_last_line(store):
    r = Run(store, None, local=[[row('a1', 'needs-reply', name='alpha', line='please answer')]])
    first = r.screens[0]
    lines = first.splitlines()
    assert lines[0].split() == ['#', 'STATE', 'TOOL', 'SESSION', 'FOLDER', 'AGE', 'LINE']
    assert 'alpha@laptop' in first and 'please answer' in first
    assert f'updated {watch.hhmmss(1_000_000.0)}' in first
    assert lines[-1] == 'number or name + Enter to continue, q + Enter to quit'
    assert r.out.getvalue().startswith(watch.CLEAR)


def test_screen_is_redrawn_every_refresh_seconds_and_at_least_two(store):
    r = Run(store, *tick(3), local=[[]])
    assert r.keys.timeouts[:3] == [5, 5, 5] and len(r.screens) == 4
    for value, want in ((1, 2), (9, 9)):
        with open(f'{store}/config.toml', 'w') as fh:
            fh.write(f'[sessions]\nrefresh = {value}\n')
        r = Run(store, None, local=[[]])
        assert r.keys.timeouts[0] == want


def test_q_quits_with_0_enter_redraws_and_end_of_input_quits(store):
    r = Run(store, '', 'q', local=[[]])
    assert r.code == 0 and len(r.screens) == 2
    r = Run(store, local=[[]])  # the script is empty: end of input
    assert r.code == 0 and len(r.screens) == 1


def test_ctrl_c_quits_with_130(store):
    r = Run(store, None, KeyboardInterrupt(), local=[[]])
    assert r.code == 130


def test_every_string_on_screen_is_cleaned(store):
    nasty = row('a1', 'needs-reply', name='x\x1b]0;title\x07y', folder='/h/\x1b[2Jdir', line='a\x1b[31mred\x00')
    r = Run(store, local=[[nasty]])
    body = r.screens[0]
    assert '\x07' not in body and '\x00' not in body and '\x1b' not in body
    assert 'xy@laptop' in body


def test_no_em_dash_in_the_module():
    for mod in (watch, notice):
        with open(mod.__file__, encoding='utf-8') as fh:
            assert EM_DASH not in fh.read()


# --- choosing a row ---

def test_choosing_a_row_runs_it_and_returns_to_the_watch(store, monkeypatch):
    ran = []
    monkeypatch.setattr(launch, 'run', lambda r, lock=None: ran.append(r.id) or 0)
    r = Run(store, '1', 'q', local=[[row('a1', 'needs-reply', pid=None)]])
    assert ran == ['a1'] and r.code == 0 and len(r.screens) == 2


def test_choosing_by_name(store, monkeypatch):
    ran = []
    monkeypatch.setattr(launch, 'run', lambda r, lock=None: ran.append(r.id) or 0)
    Run(store, 'alpha', 'q', local=[[row('a1', 'needs-reply', name='alpha', pid=None)]])
    assert ran == ['a1']


def test_a_wrong_choice_is_said_on_the_next_screen(store):
    r = Run(store, '7', 'q', local=[[row('a1')]])
    assert 'there is no line 7 in the list' in r.screens[1]


def test_a_refusal_is_shown_and_waits_for_enter(store, monkeypatch):
    r = Run(store, '1', '', 'q', local=[[row('a1', pid=77)]])  # open elsewhere: launch refuses
    assert 'is open in' in r.out.getvalue() and 'press Enter' in r.out.getvalue()
    assert r.code == 0


def test_watch_stops_redrawing_while_the_tool_runs(store, monkeypatch):
    seen_during = []

    def fake_run(r, lock=None):
        seen_during.append(r.id)
        return 0
    monkeypatch.setattr(launch, 'run', fake_run)
    r = Run(store, '1', local=[[row('a1', pid=None)]])
    assert seen_during == ['a1']


def test_choosing_a_remote_row_runs_ssh_as_a_child(store, ssh, monkeypatch):
    write_config(store, '[machines]\nwork = "me@work.example.com"\n')
    calls = []
    monkeypatch.setattr(launch, 'run_remote', lambda m, target: calls.append((m.name, target)) or 0)
    remote_row = row('w1', 'needs-reply', machine='work', pid=None)
    Run(store, '1', 'q', local=[[]], ask=lambda m, timeout=10, control=None: ([remote_row], ''), grace=2)
    assert calls == [('work', 'claude-code:w1')]


# --- other machines ---

def test_slow_machine_is_not_asked_twice_and_its_rows_are_marked(store):
    write_config(store, '[machines]\nwork = "me@work.example.com"\nfast = "me@fast.example.com"\n')
    gate = threading.Event()
    calls = []

    def ask(m, timeout=10, control=None):
        calls.append(m.name)
        if m.name == 'work' and calls.count('work') > 1:
            gate.wait(5)
        return [row(f'{m.name}-1', 'needs-reply', machine=m.name, name=m.name)], ''
    try:
        r = Run(store, *tick(3), local=[[]], ask=ask, grace=0.2)
    finally:
        gate.set()
    assert calls.count('work') == 2 and calls.count('fast') == 4
    last = r.screens[-1]
    stale = [ln for ln in last.splitlines() if 'work@work' in ln]
    assert stale and '(as of ' in stale[0]
    assert not any('(as of' in ln for ln in last.splitlines() if 'fast@fast' in ln)
    first = r.screens[0]
    assert '(as of' not in first


def test_slow_machine_with_no_answer_yet_says_so(store):
    write_config(store, '[machines]\nwork = "me@work.example.com"\n')
    gate = threading.Event()
    try:
        r = Run(store, None, local=[[]], ask=lambda m, timeout=10, control=None: (gate.wait(5), ([], ''))[1], grace=0.05)
    finally:
        gate.set()
    assert 'work: still asking' in r.screens[0]


def test_late_answer_replaces_the_marked_rows(store):
    write_config(store, '[machines]\nwork = "me@work.example.com"\n')
    gate = threading.Event()
    n = []

    def ask(m, timeout=10, control=None):
        n.append(1)
        if len(n) == 2:
            gate.wait(5)
        return [row(f'w{len(n)}', machine='work', name=f'ask{len(n)}')], ''
    try:
        keys = [None, None]
        clock = Clock()
        out = io.StringIO()

        def read(timeout):
            if keys:
                keys.pop(0)
                if len(keys) == 0:
                    gate.set()
                    time.sleep(0.2)
                clock.t += timeout
                return None
            raise EOFError
        cfg.configure(store, required=False)
        watch.run(args(), store, clock=clock, read=read, sleep=lambda s: None, out=out, local=Source([]), ask=ask,
                  send=lambda *a: None, grace=0.3)
    finally:
        gate.set()
    screens = out.getvalue().split(watch.CLEAR)[1:]
    assert 'ask1@work' in screens[0]
    assert '(as of' in screens[1] and 'ask1@work' in screens[1]
    assert 'ask3@work' in screens[2] and '(as of' not in screens[2]


def test_machine_with_a_bad_entry_is_an_error_row_and_never_asked(store):
    write_config(store, '[machines]\nbad = "-oProxyCommand=x"\n')
    asked = []
    r = Run(store, local=[[]], ask=lambda m, timeout=10, control=None: asked.append(m.name) or ([], ''))
    assert asked == [] and 'bad' in r.screens[0]


# --- the ssh logins ---

def two_machines(store):
    write_config(store, '[machines]\nwork = "me@work.example.com"\nhome = "me@home.example.com"\n')


def test_masters_open_one_after_another_and_close(store, ssh):
    two_machines(store)
    ssh.answer('me@work.example.com', login_sleep=0.15)
    ssh.answer('me@home.example.com', login_sleep=0.05)
    r = Run(store, local=[[]], ask=lambda m, timeout=10, control=None: ([], ''), grace=2)
    logins = ssh.calls('login')
    assert [c['argv'][c['argv'].index('--') + 1] for c in logins] == ['me@work.example.com', 'me@home.example.com']
    assert logins[0]['end'] <= logins[1]['start']
    for c in logins:
        text = ' '.join(c['argv'])
        assert 'ControlMaster=yes' in text and 'ControlPath=' in text
    exits = ssh.calls('exit')
    assert len(exits) == 2 and all('-O' in c['argv'] and 'exit' in c['argv'] for c in exits)
    folder = os.path.dirname(next(w for w in logins[0]['argv'] if w.startswith('ControlPath=')).split('=', 1)[1])
    assert not os.path.exists(folder)
    assert r.code == 0


def test_masters_are_closed_even_when_the_loop_raises(store, ssh):
    two_machines(store)
    with pytest.raises(RuntimeError):
        Run(store, None, None, local=[[], RuntimeError('boom')], ask=lambda m, timeout=10, control=None: ([], ''),
            grace=2)
    assert len(ssh.calls('exit')) == 2
    logins = ssh.calls('login')
    folder = os.path.dirname(next(w for w in logins[0]['argv'] if w.startswith('ControlPath=')).split('=', 1)[1])
    assert not os.path.exists(folder)


def test_masters_are_closed_on_ctrl_c(store, ssh):
    two_machines(store)
    r = Run(store, KeyboardInterrupt(), local=[[]], ask=lambda m, timeout=10, control=None: ([], ''), grace=2)
    assert r.code == 130 and len(ssh.calls('exit')) == 2


def test_failed_login_is_an_error_row_and_not_asked_again(store, ssh):
    two_machines(store)
    ssh.answer('me@work.example.com', login_exit=255)
    asked = []

    def ask(m, timeout=10, control=None):
        asked.append(m.name)
        return [], ''
    r = Run(store, None, None, local=[[]], ask=ask, grace=2)
    assert set(asked) == {'home'} and len(asked) == 3
    assert 'work: cannot log in' in r.screens[0] and 'work: cannot log in' in r.screens[2]
    exits = ssh.calls('exit')
    assert [c['argv'][c['argv'].index('--') + 1] for c in exits] == ['me@home.example.com']


def test_every_ask_gets_the_control_path_and_keeps_batch_mode(store, ssh):
    two_machines(store)
    body = {'ok': True, 'command': 'resume', 'format': sessions.FORMAT, 'machine': 'x', 'rows': [], 'errors': []}
    ssh.answer('me@work.example.com', stdout=json.dumps(body))
    ssh.answer('me@home.example.com', stdout=json.dumps(body))
    Run(store, None, local=[[]], grace=3)
    asks = ssh.calls('ask')
    assert len(asks) == 4
    login_paths = {w for c in ssh.calls('login') for w in c['argv'] if w.startswith('ControlPath=')}
    assert len(login_paths) == 1
    for c in asks:
        assert 'BatchMode=yes' in c['argv'] or 'BatchMode=yes' in ' '.join(c['argv'])
        assert {w for w in c['argv'] if w.startswith('ControlPath=')} == login_paths


def test_local_watch_opens_no_login(store, ssh):
    two_machines(store)
    Run(store, local=[[]], extra=('--local',))
    assert ssh.calls() == []


def test_machine_option_limits_the_watch(store, ssh):
    two_machines(store)
    asked = []
    Run(store, local=[[row('l1')]], extra=('--machine', 'home'),
        ask=lambda m, timeout=10, control=None: asked.append(m.name) or ([], ''), grace=2)
    assert asked == ['home'] and [c['argv'][c['argv'].index('--') + 1] for c in ssh.calls('login')] == ['me@home.example.com']


# --- --json --watch ---

def run_json(store, cycles, capsys, **kw):
    clock = Clock()
    cfg.configure(store, required=False)
    code = watch.run(args('--json'), store, clock=clock, sleep=lambda s: setattr(clock, 't', clock.t + s),
                     local=Source(*cycles), send=lambda *a: None, grace=0.05, max_cycles=len(cycles), **kw)
    lines = capsys.readouterr().out.splitlines()
    return code, [json.loads(x) for x in lines]


def test_json_watch_prints_one_envelope_per_change(store, capsys):
    a, b = row('a1', 'working'), row('a1', 'needs-reply')
    code, out = run_json(store, [[a], [a], [b], [b], []], capsys)
    assert code == 0 and len(out) == 3
    assert all(o['ok'] is True and o['command'] == 'resume' and o['format'] == sessions.FORMAT for o in out)
    assert [len(o['rows']) for o in out] == [1, 1, 0]
    assert out[0]['rows'][0]['state'] == 'working' and out[1]['rows'][0]['state'] == 'needs-reply'


def test_json_watch_needs_no_terminal_and_sends_notices(store, capsys, monkeypatch):
    sent = []
    clock = Clock()
    cfg.configure(store, required=False)
    watch.run(args('--json'), store, clock=clock, sleep=lambda s: None,
              local=Source([row('a1', 'working')], [row('a1', 'needs-reply')]), send=lambda t, m: sent.append(m),
              max_cycles=2)
    assert sent == ['claude-code demo on laptop: needs a reply']


def test_json_watch_errors_are_in_the_envelope(store, capsys):
    write_config(store, '[machines]\nbad = "-oProxyCommand=x"\n')
    code, out = run_json(store, [[]], capsys)
    assert out[0]['errors'] and out[0]['errors'][0]['machine'] == 'bad'


# --- the command ---

def test_watch_without_a_terminal_exits_2(store, capsys, monkeypatch):
    monkeypatch.setattr(sessions, 'supported', lambda: True)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(''))
    assert cli.main(['resume', '--store', store, '--watch']) == 2
    err = capsys.readouterr()
    assert err.out == '' and 'needs a terminal' in err.err and '--json --watch' in err.err


def test_watch_with_a_target_or_dry_run_is_a_usage_error(store, capsys, monkeypatch):
    monkeypatch.setattr(sessions, 'supported', lambda: True)
    for extra in (['somename'], ['-n']):
        assert cli.main(['resume', '--store', store, '--watch', '--json', *extra]) == 2
        assert capsys.readouterr().err


def test_cli_json_watch_runs_without_a_terminal(store, capsys, monkeypatch):
    monkeypatch.setattr(sessions, 'supported', lambda: True)
    monkeypatch.setattr(sessions, 'local_rows', lambda line=True, machine=None: [row('a1', 'working')])
    monkeypatch.setattr(watch, 'MAX_CYCLES', 2)
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    assert cli.main(['resume', '--store', store, '--watch', '--json', '--local']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])['rows'][0]['id'] == 'a1'


def test_capability_and_the_old_message_is_gone():
    assert 'resume.watch' in cli.CAPABILITIES and list(cli.CAPABILITIES) == sorted(cli.CAPABILITIES)


# --- the [sessions] table ---

def test_sessions_config_defaults_and_values(store, capsys):
    assert cfg.sessions_config(store) == {'refresh': 5, 'notify': True}
    write_config(store, '[sessions]\nrefresh = 7\nnotify = false\nextra = "kept"\n')
    got = cfg.sessions_config(store)
    assert got['refresh'] == 7 and got['notify'] is False and got['extra'] == 'kept'
    assert capsys.readouterr().err == ''


def test_sessions_config_wrong_type_gives_the_default_and_one_warning(store, capsys):
    write_config(store, '[sessions]\nrefresh = "fast"\nnotify = 1\n')
    got = cfg.sessions_config(store)
    assert got == {'refresh': 5, 'notify': True}
    err = capsys.readouterr().err
    assert err.count('refresh must be a whole number') == 1 and err.count('notify must be true or false') == 1


def test_sessions_config_bool_is_not_a_number_and_small_refresh_is_raised(store, capsys):
    write_config(store, '[sessions]\nrefresh = true\n')
    assert cfg.sessions_config(store)['refresh'] == 5 and 'refresh must be' in capsys.readouterr().err
    with open(f'{store}/config.toml', 'w') as fh:
        fh.write('[sessions]\nrefresh = 0\n')
    assert cfg.sessions_config(store)['refresh'] == 2


def test_sessions_config_without_a_file(tmp_path):
    assert cfg.sessions_config(str(tmp_path / 'nothing')) == {'refresh': 5, 'notify': True}


def test_template_has_the_sessions_table():
    import tomllib
    from importlib import resources
    text = (resources.files('llmpro') / 'templates' / 'config.toml').read_text(encoding='utf-8')
    got = tomllib.loads(text)['sessions']
    assert got == {'refresh': 5, 'notify': True}
    lines = [ln for ln in text.splitlines() if ln.startswith(('refresh', 'notify'))]
    assert len(lines) == 2 and all('#' in ln for ln in lines)
