"""Package 20: `llmpro resume TARGET` takes over an idle session. The process side is a real child process that
the test starts (python sleeping, with the slave side of a pseudo-terminal as its stdin), so the signals are
real and harmless: they go to that child only, through a pidfd. llmpro sees it through a fake /proc. Nothing
here touches a real claude, copilot, opencode or ssh, or the real home."""
import builtins
import dataclasses
import json
import os
import re
import signal
import subprocess
import sys
import time

import pytest

from llmpro import cli, sessions
from llmpro import store as cfg
from llmpro.sessions import launch, takeover

AID = 'aaaaaaaa-1111-4111-8111-111111111111'
NOW = 2_000_000_000  # a fixed "now" for the checks that take one
START = 5000
OLD = 1_000_000_000  # long ago, for files and times that must look quiet whatever the real clock says
SLEEPER = 'import signal, time\n{setup}print("ready", flush=True)\ntime.sleep(300)\n'

FAKE_TOOL = '''#!{python}
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, 'claude.log'), 'a') as fh:
    fh.write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd()}}) + '\\n')
'''


@pytest.fixture(autouse=True)
def state(home, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    for name in list(os.environ):
        if name.startswith(('VSCODE_', 'CLAUDE', 'COPILOT_', 'OPENCODE')) or name in ('TERM_PROGRAM', 'AI_AGENT'):
            monkeypatch.delenv(name, raising=False)
    sessions.begin()
    yield tmp_path / 'state'
    cfg.STORE = ''


@pytest.fixture
def proc(tmp_path, monkeypatch):
    root = tmp_path / 'proc'
    (root / 'self').mkdir(parents=True)  # sessions.supported() looks for it
    monkeypatch.setattr(sessions, 'PROC', str(root))
    return root


def add_proc(root, pid, program='claude', tty='/dev/null', children=(), start=START):
    """A fake /proc/<pid>: its program, its standard input and the pids of its children."""
    d = root / str(pid)
    (d / 'fd').mkdir(parents=True, exist_ok=True)
    (d / 'task' / str(pid)).mkdir(parents=True, exist_ok=True)
    (d / 'cmdline').write_bytes(program.encode() + b'\0')
    (d / 'comm').write_text(program + '\n')
    (d / 'stat').write_text(f'{pid} ({program}) S 1 ' + ' '.join(['0'] * 17) + f' {start} 0 0\n')
    (d / 'task' / str(pid) / 'children').write_text(' '.join(str(c) for c in children))
    if (d / 'fd' / '0').is_symlink():
        (d / 'fd' / '0').unlink()
    os.symlink(tty, d / 'fd' / '0')


class Child:
    """A process the test started. Only this object's pid is ever signalled."""
    def __init__(self, setup=''):
        self.master, self.slave = os.openpty()
        self.tty = os.ttyname(self.slave)
        self.popen = subprocess.Popen([sys.executable, '-c', SLEEPER.format(setup=setup)], stdin=self.slave,
                                      stdout=subprocess.PIPE, text=True)
        assert self.popen.stdout.readline().strip() == 'ready'
        self.pid = self.popen.pid

    def state(self):
        """The state letter in the real /proc/<pid>/stat."""
        with open(f'/proc/{self.pid}/stat') as fh:
            text = fh.read()
        return text[text.rindex(')') + 2]

    def stopped(self):
        """The state letter once a stop has taken effect (the kernel stops the process a moment after the
        signal), or whatever it is after a second."""
        for _ in range(100):
            if self.state() == 'T':
                break
            time.sleep(0.01)
        return self.state()

    def alive(self):
        return self.popen.poll() is None

    def close(self):
        try:
            if self.popen.poll() is None:
                self.popen.send_signal(signal.SIGCONT)
                self.popen.kill()
            self.popen.wait(timeout=10)
        finally:
            self.popen.stdout.close()
            os.close(self.master)
            os.close(self.slave)


@pytest.fixture
def make_child():
    made = []

    def make(setup=''):
        made.append(Child(setup))
        return made[-1]
    yield make
    for child in made:
        child.close()


@pytest.fixture
def tools(tools_dir):
    path = tools_dir / 'claude'
    path.write_text(FAKE_TOOL.format(python=sys.executable))
    path.chmod(0o755)
    return tools_dir


def calls(tools_dir):
    log = tools_dir / 'claude.log'
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def files(tmp_path):
    """A record and a log, both old."""
    out = []
    for name in ('record.json', 'log.jsonl'):
        path = tmp_path / name
        path.write_text('{}')
        os.utime(path, (OLD, OLD))
        out.append(str(path))
    return tuple(out)


def row(files=None, **over):
    base = dict(tool='claude-code', machine=cfg.MACHINE, id=AID, name='demo', folder='/nonexistent',
                state='needs-reply', since=NOW - 120, line='', pid=4242, where='terminal', version='2.1',
                local_only=False, error='', paths=files)
    base.update(over)
    return sessions.Row(**base)


def listing(monkeypatch, *rows):
    monkeypatch.setattr(sessions, 'local_rows', lambda line=True, machine=None: list(rows))


def fake_now(monkeypatch, step=0.0):
    """A clock far in the future, so the terminal of a new child looks quiet. step > 0 makes it tick."""
    ticks = [0.0]

    def clock():
        ticks[0] += step
        return time.time() + 1000 + ticks[0]
    monkeypatch.setattr(takeover, 'clock', clock)


def run(args, capsys):
    code = cli.main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def idle(proc, make_child, files, tools, tmp_path, monkeypatch):
    """A real child that looks like an idle Claude session, listed as such. -> (child, row)."""
    child = make_child()
    add_proc(proc, child.pid, tty=child.tty)
    folder = tmp_path / 'work'
    folder.mkdir()
    r = row(files, pid=child.pid, folder=str(folder), since=OLD)
    fake_now(monkeypatch)

    def rows(line=True, machine=None):
        return [r] if child.alive() else []
    monkeypatch.setattr(sessions, 'local_rows', rows)
    return child, r


# ---------------------------------------------------------------- check

def test_check_passes_for_an_idle_session(proc, files, tmp_path, monkeypatch):
    fake = tmp_path / 'fake-tty'
    fake.write_text('')
    os.utime(fake, (OLD, OLD))
    add_proc(proc, 4242)
    monkeypatch.setattr(takeover, '_tty', lambda pid: str(fake))
    monkeypatch.setattr(takeover, 'PTS', re.compile('.*'))
    assert takeover.check(row(files), NOW) == []


@pytest.mark.parametrize('state', ['needs-approval', 'working', 'unknown'])
def test_check_refuses_other_states(proc, files, state):
    add_proc(proc, 4242)
    reasons = takeover.check(row(files, state=state), NOW)
    assert f'it is {state}; only a session waiting for a reply is taken over' in reasons


def test_check_ten_second_rule(proc, files):
    add_proc(proc, 4242)
    assert any('less than 10s' in r for r in takeover.check(row(files, since=NOW - 5), NOW))
    assert not any('less than' in r for r in takeover.check(row(files, since=NOW - 11), NOW))
    assert any('less than' in r for r in takeover.check(row(files, since=None), NOW))


def test_check_record_or_log_changed_lately(proc, files):
    add_proc(proc, 4242)
    os.utime(files[1], (NOW - 4, NOW - 4))
    assert 'its record or log changed 4s ago' in takeover.check(row(files), NOW)
    os.utime(files[1], (NOW - 30, NOW - 30))
    assert not any('changed' in r for r in takeover.check(row(files), NOW))


def test_check_terminal_must_be_a_pts(proc, files):
    add_proc(proc, 4242, tty='/dev/null')
    assert 'it is not in a terminal llmpro can check' in takeover.check(row(files), NOW)


def test_check_recent_terminal_use(proc, files, tmp_path, monkeypatch):
    add_proc(proc, 4242)
    fake = tmp_path / 'fake-tty'
    fake.write_text('')
    monkeypatch.setattr(takeover, '_tty', lambda pid: str(fake))
    monkeypatch.setattr(takeover, 'PTS', re.compile('.*'))
    os.utime(fake, (NOW - 20, NOW - 20))
    assert 'someone typed in it 20s ago' in takeover.check(row(files), NOW)
    os.utime(fake, (NOW - 61, NOW - 90))
    assert takeover.check(row(files), NOW) == []
    os.utime(fake, (NOW - 90, NOW - 10))  # the modify time counts too
    assert 'someone typed in it 10s ago' in takeover.check(row(files), NOW)


def test_check_children(proc, files):
    add_proc(proc, 4242, children=(7001, 7002))
    add_proc(proc, 7001, program='make')
    add_proc(proc, 7002, program='sleep')
    reasons = takeover.check(row(files), NOW)
    assert 'it has 2 child processes (make, sleep); they would end with it' in reasons


def test_check_children_that_cannot_be_read(proc, files):
    add_proc(proc, 4242)
    os.unlink(proc / '4242' / 'task' / '4242' / 'children')
    assert 'its child processes cannot be read' in takeover.check(row(files), NOW)


def test_check_needs_a_verified_pid(proc, files):
    reasons = takeover.check(row(files, pid=4242), NOW)  # no such process
    assert reasons == ['it has no live process llmpro can verify']
    add_proc(proc, 4242, program='vim')
    assert 'it has no live process llmpro can verify' in takeover.check(row(files), NOW)
    assert 'it has no live process llmpro can verify' in takeover.check(row(files, pid=None), NOW)


def test_check_container_and_opencode(proc, files):
    add_proc(proc, 4242)
    assert 'it runs in a container' in takeover.check(row(files, where='container'), NOW)
    reasons = takeover.check(row(files, tool='opencode'), NOW)
    assert len(reasons) == 1 and 'never taken over' in reasons[0]


# ---------------------------------------------------------------- run

def test_happy_path(idle, tools, store, capsys, monkeypatch):
    child, r = idle
    seen_states = []
    real_check = takeover.check

    def check(row, now):
        seen_states.append(child.state() if not seen_states else child.stopped())  # before the stop, then after it
        return real_check(row, now)
    monkeypatch.setattr(takeover, 'check', check)
    held = []
    real_run = launch.run

    def run_here(row, lock=None):
        held.append(lock)
        with pytest.raises(launch.Busy):  # the lock from step 1 is still held
            with launch.lock(row):
                pass
        assert not child.alive()
        return real_run(row, lock=lock)
    monkeypatch.setattr(launch, 'run', run_here)
    code, out, err = run(['resume', '--store', store, '-y', 'demo'], capsys)
    assert code == 0, err
    assert seen_states[0] != 'T' and seen_states[-1] == 'T'
    assert f'to continue it elsewhere later: llmpro resume claude-code:{AID}' in out
    assert child.popen.wait(timeout=5) == -signal.SIGHUP
    assert held and held[0] is not None
    (call,) = calls(tools)
    assert call['argv'] == ['--resume', AID] and call['cwd'] == r.folder


def test_asks_once_with_the_question(idle, tools, store, capsys, monkeypatch):
    child, r = idle
    asked = []
    monkeypatch.setattr(sessions, '_interactive', lambda: True)
    monkeypatch.setattr(builtins, 'input', lambda prompt='': asked.append(prompt) or 'y')
    code, out, err = run(['resume', '--store', store, 'demo'], capsys)
    assert code == 0, err
    (question,) = asked
    assert question.startswith(f'Close claude-code demo (pid {child.pid}, terminal, {cfg.MACHINE}, idle ')
    assert question.endswith(', no child processes) and continue it here? Anything typed there and not sent is '
                             'lost. [y/N] ')
    assert len(calls(tools)) == 1


def test_a_no_leaves_everything_alone(idle, tools, store, capsys, monkeypatch):
    child, r = idle
    monkeypatch.setattr(sessions, '_interactive', lambda: True)
    monkeypatch.setattr(builtins, 'input', lambda prompt='': 'n')
    code, out, err = run(['resume', '--store', store, 'demo'], capsys)
    assert code == 1 and child.alive() and child.state() != 'T' and calls(tools) == []


def test_no_terminal_and_no_yes_exits_2(idle, tools, store, capsys):
    child, r = idle
    code, out, err = run(['resume', '--store', store, 'demo'], capsys)
    assert code == 2 and 'no terminal' in err and '-y' in err
    assert child.alive() and child.state() != 'T' and calls(tools) == []


def test_refusal_exits_1_and_lists_every_reason(idle, proc, tools, store, capsys, monkeypatch):
    child, r = idle
    add_proc(proc, child.pid, tty='/dev/null')
    listing_row = dataclasses.replace(r, state='working')
    listing(monkeypatch, listing_row)
    code, out, err = run(['resume', '--store', store, '-y', 'demo'], capsys)
    assert code == 1 and out == ''
    assert 'it is working; only a session waiting for a reply is taken over' in err
    assert 'it is not in a terminal llmpro can check' in err
    assert 'close it there first' in err
    assert child.alive() and child.state() != 'T' and calls(tools) == []


def test_lock_held_by_another_llmpro(idle, tools, store, capsys):
    child, r = idle
    with launch.lock(r):
        code, out, err = run(['resume', '--store', store, '-y', 'demo'], capsys)
    assert code == 1 and 'busy with another llmpro' in err
    assert child.alive() and child.state() != 'T' and calls(tools) == []


def test_child_that_ignores_sighup_is_left_running(idle, proc, make_child, tools, store, capsys, monkeypatch):
    child = make_child('signal.signal(signal.SIGHUP, signal.SIG_IGN)\n')
    add_proc(proc, child.pid, tty=child.tty)
    r = dataclasses.replace(idle[1], pid=child.pid)
    listing(monkeypatch, r)
    fake_now(monkeypatch, step=1.5)
    code, out, err = run(['resume', '--store', store, '-y', 'demo'], capsys)
    assert code == 1 and f'pid {child.pid} did not exit; nothing else was done' in err
    assert child.alive()  # no SIGKILL
    for _ in range(100):  # SIGCONT was sent
        if child.state() != 'T':
            break
        time.sleep(0.02)
    assert child.state() != 'T'
    assert calls(tools) == []


def test_change_while_stopped_sends_only_sigcont(idle, tools, store, capsys, monkeypatch):
    child, r = idle
    looks = []
    moved = r

    def rows(line=True, machine=None):
        looks.append(child.state() if len(looks) < 2 else child.stopped())
        if len(looks) > 2:  # the third look is with the process stopped
            os.utime(r.paths[0], (OLD + 5, OLD + 5))  # not recent, only different
        return [moved]
    monkeypatch.setattr(sessions, 'local_rows', rows)
    code, out, err = run(['resume', '--store', store, '-y', 'demo'], capsys)
    assert code == 1 and 'it was not taken over' in err
    assert looks[-1] == 'T'
    assert 'changed while it was being stopped' in err
    assert 'to continue it elsewhere later' not in out
    assert child.alive()
    for _ in range(100):
        if child.state() != 'T':
            break
        time.sleep(0.02)
    assert child.state() != 'T' and calls(tools) == []


def test_session_that_changed_state_while_stopped(idle, tools, store, capsys, monkeypatch):
    child, r = idle
    answers = iter([r, dataclasses.replace(r, state='working')])
    monkeypatch.setattr(sessions, 'local_rows', lambda line=True, machine=None: [next(answers)])
    code, out, err = run(['resume', '--store', store, '-y', 'demo'], capsys)
    assert code == 1 and 'it is working' in err and child.alive()
    assert calls(tools) == []


# ---------------------------------------------------------------- -n and other routes

def test_dry_run_takes_no_lock_and_sends_no_signal(idle, tools, store, capsys):
    child, r = idle
    code, out, err = run(['resume', '--store', store, '-n', 'demo'], capsys)
    assert code == 0 and 'check: it may go' in out and 'plan:' in out
    assert child.alive() and child.state() != 'T' and calls(tools) == []
    assert not os.path.exists(os.path.join(os.environ['XDG_STATE_HOME'], 'llmpro', 'live'))


def test_dry_run_shows_the_reasons(idle, store, capsys, monkeypatch):
    child, r = idle
    listing(monkeypatch, dataclasses.replace(r, state='working'))
    code, out, err = run(['resume', '--store', store, '-n', 'demo'], capsys)
    assert code == 1 and 'check: refused' in out and 'it is working' in out
    assert child.alive() and child.state() != 'T'


def test_container_row_is_refused_as_before(tools, store, capsys, monkeypatch):
    listing(monkeypatch, row(pid=77, where='container'))
    code, out, err = run(['resume', '--store', store, '-y', 'demo'], capsys)
    assert code == 1 and 'runs in a container; continue it there' in err


def test_opencode_session_is_never_taken_over(proc, files, store, capsys, monkeypatch):
    listing(monkeypatch, row(files, tool='opencode', id='ses_abc123'))
    monkeypatch.setattr(takeover, '_close', lambda *a: pytest.fail('a signal path was reached'))
    code, out, err = run(['resume', '--store', store, '-y', 'demo'], capsys)
    assert code == 1 and 'never taken over' in err


def test_remote_session_is_not_taken_over_from_here(store, capsys, monkeypatch):
    with open(f'{store}/config.toml', 'a', encoding='utf-8') as fh:
        fh.write('\n[machines]\nwork = "me@work.example.com"\n')
    r = row(machine='work', pid=88, where='ssh')
    monkeypatch.setattr(sessions, 'all_rows', lambda **kw: ([r], []))
    monkeypatch.setattr(takeover, 'run', lambda *a, **kw: pytest.fail('takeover ran here'))
    sent = []
    monkeypatch.setattr(launch, 'run_remote', lambda m, target: sent.append((m.name, target)) or 0)
    code, out, err = run(['resume', '--store', store, 'demo'], capsys)
    assert code == 0 and sent == [('work', f'claude-code:{AID}')]


def test_session_closed_since_the_list_is_not_continued(idle, tools, store, capsys, monkeypatch):
    child, r = idle
    answers = iter([[r], []])
    monkeypatch.setattr(sessions, 'local_rows', lambda line=True, machine=None: next(answers))
    code, out, err = run(['resume', '--store', store, '-y', 'demo'], capsys)
    assert code == 1 and 'look again' in err and child.alive() and calls(tools) == []


def test_a_reused_pid_is_never_stopped(idle, tools, store, capsys, monkeypatch):
    child, r = idle
    real_verified, real_open = takeover._verified, os.pidfd_open
    held = []

    def pidfd_open(pid, *a):
        held.append(pid)
        return real_open(pid, *a)

    def verified(row):
        info = real_verified(row)
        if held and info is not None:  # after the pidfd is taken: another process with this pid
            return (info[0], info[1], info[2] + 1)
        return info
    monkeypatch.setattr(os, 'pidfd_open', pidfd_open)
    monkeypatch.setattr(takeover, '_verified', verified)
    stops = []
    monkeypatch.setattr(takeover, '_send', lambda fd, sig: stops.append(sig) or True)
    code, out, err = run(['resume', '--store', store, '-y', 'demo'], capsys)
    assert code == 1 and 'not the one that was checked' in err
    assert held == [child.pid] and stops == [] and child.alive() and calls(tools) == []
