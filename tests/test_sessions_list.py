"""Package 15: `llmpro resume` lists the open sessions on this machine. A fake /proc, a temporary HOME and a
fake `opencode` stand in for the real ones; nothing here reads the real /proc or home."""
import builtins
import io
import json
import os
import subprocess
import sys
import time

import pytest

from llmpro import cli, sessions
from llmpro import store as cfg
from llmpro.sessions import claude, copilot, opencode

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'sessions')
MID = 'machine-aaa'
OWN_NS = '4026531836'
SID = '11111111-1111-4111-8111-111111111111'
CID = '22222222-2222-4222-8222-222222222222'
TOKEN = 'ghp_' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8'


def fixture(name):
    with open(os.path.join(FIX, name), encoding='utf-8') as fh:
        return fh.read()


class FakeProc:
    def __init__(self, root):
        self.root = root
        (root / 'self' / 'ns').mkdir(parents=True)
        os.symlink(f'pid:[{OWN_NS}]', root / 'self' / 'ns' / 'pid')

    def add(self, pid, argv, start=5000, ppid=1, comm='MainThread', cwd=None, ns=None, nspid=None):
        d = self.root / str(pid)
        (d / 'ns').mkdir(parents=True)
        (d / 'cmdline').write_bytes(b''.join(w.encode() + b'\0' for w in argv))
        (d / 'comm').write_text(comm + '\n')
        (d / 'stat').write_text(f'{pid} ({comm}) S {ppid} ' + ' '.join(['0'] * 17) + f' {start} 0 0\n')
        (d / 'status').write_text(f'Name:\t{comm}\nNSpid:\t' + '\t'.join(str(n) for n in (nspid or [pid])) + '\n')
        os.symlink(f'pid:[{ns or OWN_NS}]', d / 'ns' / 'pid')
        if cwd:
            os.symlink(cwd, d / 'cwd')


@pytest.fixture(autouse=True)
def proc(tmp_path, monkeypatch):
    root = tmp_path / 'proc'
    root.mkdir()
    machine_id = tmp_path / 'machine-id'
    machine_id.write_text(MID + '\n')
    monkeypatch.setattr(sessions, 'PROC', str(root))
    monkeypatch.setattr(sessions, 'MACHINE_ID_FILE', str(machine_id))
    sessions.begin()
    return FakeProc(root)


def put_claude(home, pid=4242, **over):
    rec = json.loads(fixture('claude_record.json'))
    rec['pid'] = pid
    rec.update(over)
    rec = {k: v for k, v in rec.items() if v is not None}
    d = home / '.claude' / 'sessions'
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{pid}.json').write_text(json.dumps(rec))
    return rec


def put_transcript(home, sid=SID, text=None):
    d = home / '.claude' / 'projects' / '-demo'
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{sid}.jsonl').write_text(text if text is not None else fixture('claude_transcript.jsonl'))
    return d / f'{sid}.jsonl'


def put_copilot(home, sid=CID, pid=None, events=None, cwd=None):
    d = home / '.copilot' / 'session-state' / sid
    d.mkdir(parents=True, exist_ok=True)
    if pid:
        (d / f'inuse.{pid}.hold').write_text('')
        (d / f'inuse.{pid}.lock').write_text('12345678')
    if events is not False:
        text = fixture('copilot_events.jsonl') if events is None else events
        if cwd:
            text = text.replace('~/code/demo', cwd)
        (d / 'events.jsonl').write_text(text)
    return d


def event(kind, **data):
    return json.dumps({'type': kind, 'timestamp': '2026-10-21T09:00:00Z', 'data': data}) + '\n'


def copilot_with_tail(*lines):
    return fixture('copilot_events.jsonl').split('session.usage_checkpoint')[0].rsplit('\n', 1)[0] + '\n' \
        + ''.join(lines)


# ---------------------------------------------------------------- rows, clean, sort

def make_row(**over):
    base = dict(tool='claude-code', machine='work', id='abc', name='n', folder='~/code/demo', state='idle',
                since=10, line='', pid=5, where='terminal', version='1', local_only=False, error='')
    base.update(over)
    return sessions.Row(**base)


def test_row_round_trip_and_paths_left_out():
    r = make_row(paths=('a', 'b'))
    d = r.as_dict()
    assert 'paths' not in d and sessions.Row.from_dict(d) == make_row()
    d2 = dict(d, since=None, pid=None)
    assert sessions.Row.from_dict(d2).pid is None


@pytest.mark.parametrize('change', [
    {'machine': None}, {'pid': '5'}, {'pid': True}, {'since': 1.5}, {'local_only': 1}, {'name': 3}, {'error': None}])
def test_row_from_dict_refuses_wrong_type(change):
    d = make_row().as_dict()
    d.update(change)
    with pytest.raises(ValueError):
        sessions.Row.from_dict(d)


def test_row_from_dict_refuses_missing_key_and_non_dict():
    d = make_row().as_dict()
    del d['version']
    with pytest.raises(ValueError):
        sessions.Row.from_dict(d)
    with pytest.raises(ValueError):
        sessions.Row.from_dict([])


def test_sort_by_state_then_oldest_first_none_last():
    rows = [make_row(id='w', state='working', since=1), make_row(id='r2', state='needs-reply', since=50),
            make_row(id='r1', state='needs-reply', since=20), make_row(id='rn', state='needs-reply', since=None),
            make_row(id='a', state='needs-approval', since=90), make_row(id='x', state='not-readable', since=None),
            make_row(id='u', state='unknown', since=3)]
    assert [r.id for r in sessions.sort(rows)] == ['a', 'r1', 'r2', 'rn', 'w', 'u', 'x']
    assert sessions.STATES == ('needs-approval', 'needs-reply', 'working', 'unknown', 'not-readable')
    assert sessions.FORMAT == 'llmpro-sessions.v1'


def test_clean_removes_controls_escapes_and_joins_lines():
    assert sessions.clean('a\x1b[31mred\x1b[0m b') == 'a' + 'red b'
    assert sessions.clean('x\x1b]0;title\x07y') == 'xy'
    assert sessions.clean('x\x1b]8;;http://example.com\x1b\\y') == 'xy'
    assert sessions.clean('one\ntwo\r\nthree') == 'one two three'
    assert sessions.clean('a\x00b\x7fc\x85d\x9b31me') == 'abc de'
    assert sessions.clean('abcdef', 3) == 'abc'
    assert sessions.clean(None) == ''
    assert sessions.ID.fullmatch('ab-12') and sessions.ID.fullmatch('ses_ab12') and not sessions.ID.fullmatch('a/b') and not sessions.ID.fullmatch('')


def test_proc_reads_program_start_and_never_comm_when_cmdline_exists(proc):
    proc.add(10, ['/usr/bin/copilot', '--x'], start=777, comm='MainThread')
    proc.add(11, [], start=5, comm='kworker')
    uid, program, start = sessions._proc(10)
    assert (uid, program, start) == (os.getuid(), 'copilot', 777)
    assert sessions._proc(11)[1] == 'kworker'
    assert sessions._proc(99) is None


def test_supported_needs_proc_self(proc, monkeypatch):
    assert sessions.supported()
    monkeypatch.setattr(sessions, 'PROC', str(proc.root / 'nowhere'))
    assert not sessions.supported()


# ---------------------------------------------------------------- Claude

def test_claude_live_record_and_transcript_line(home, proc):
    proc.add(4242, ['claude'], start=5000)
    put_claude(home)
    path = put_transcript(home)
    [r] = claude.rows('work')
    assert (r.tool, r.machine, r.id, r.name, r.folder) == ('claude-code', 'work', SID, 'demo', '~/code/demo')
    assert (r.state, r.pid, r.version, r.where, r.local_only, r.error) == ('needs-reply', 4242, '2.1.305',
                                                                            'terminal', False, '')
    assert r.since == 1790000000
    assert r.line == 'I added the test and it passes. Next I can fix the docs.'
    assert r.paths == (str(home / '.claude/sessions/4242.json'), str(path))


def test_claude_dead_reused_and_node(home, proc):
    put_claude(home, pid=1)  # no such process
    put_claude(home, pid=2, sessionId='33333333-3333-4333-8333-333333333333')
    proc.add(2, ['claude'], start=9999)  # reused: another start time
    put_claude(home, pid=3, sessionId='44444444-4444-4444-8444-444444444444')
    proc.add(3, ['/usr/bin/node', 'cli.js'], start=5000)  # node runs it
    put_claude(home, pid=4, sessionId='55555555-5555-4555-8555-555555555555')
    proc.add(4, ['bash'], start=5000)  # reused by another program
    put_claude(home, pid=5, sessionId='66666666-6666-4666-8666-666666666666', procStart=None)
    proc.add(5, ['node', 'other.js'], start=5000)  # node, and no procStart to prove it is Claude
    put_claude(home, pid=6, sessionId='77777777-7777-4777-8777-777777777777', procStart=None)
    proc.add(6, ['claude'], start=5000)  # claude itself, an older record without procStart
    assert sorted(r.pid for r in claude.rows('work')) == [3, 6]


def test_claude_process_of_another_user_is_skipped(home, proc, monkeypatch):
    proc.add(4242, ['claude'])
    put_claude(home)
    real = sessions._proc
    monkeypatch.setattr(sessions, '_proc', lambda pid: (os.getuid() + 1,) + real(pid)[1:])
    assert claude.rows('work') == []


def test_claude_status_mapping(home, proc):
    for pid, status, state in ((20, 'idle', 'needs-reply'), (21, 'busy', 'working'), (22, 'waiting', 'needs-approval'),
                               (23, 'strange', 'unknown')):
        proc.add(pid, ['claude'])
        put_claude(home, pid=pid, status=status, sessionId=f'66666666-6666-4666-8666-6666666666{pid}')
    got = {r.pid: r for r in claude.rows('work')}
    assert {p: r.state for p, r in got.items()} == {20: 'needs-reply', 21: 'working', 22: 'needs-approval',
                                                    23: 'unknown'}
    assert got[22].line == 'permission prompt'
    assert got[21].line == ''


def test_claude_missing_key_wrong_type_and_bad_id_are_not_readable(home, proc):
    proc.add(4242, ['claude'])
    put_claude(home, cwd=None)
    [r] = claude.rows('work')
    assert r.state == 'not-readable' and 'cwd' in r.error and r.id == SID
    put_claude(home, cwd=7)
    assert 'wrong type' in claude.rows('work')[0].error
    put_claude(home, sessionId='../evil')
    [r] = claude.rows('work')
    assert r.state == 'not-readable' and r.id == '' and 'not allowed' in r.error
    (home / '.claude/sessions/9.json').write_text('{ half')
    assert sorted(r.error for r in claude.rows('work'))[0].startswith('the record is not valid JSON')


def test_claude_never_opens_key_files(home, proc):
    proc.add(4242, ['claude'])
    put_claude(home)
    (home / '.claude/sessions/4242.key').write_text('not a real secret')
    assert len(claude.rows('work')) == 1


def test_claude_pid_domains(home, proc):
    # a foreign machine-id is skipped
    proc.add(31, ['claude'])
    put_claude(home, pid=31, pidDomain=f'linux:other-machine:pid:[{OWN_NS}]')
    assert claude.rows('work') == []
    # no pidDomain: the pid is a pid here
    put_claude(home, pid=31, pidDomain=None)
    assert [r.pid for r in claude.rows('work')] == [31]
    # an empty machine-id in another namespace: found through NSpid, shown as a container
    (home / '.claude/sessions/31.json').unlink()
    proc.add(88, ['claude'], ns='4026539999', nspid=[88, 7])
    put_claude(home, pid=7, pidDomain='linux::pid:[4026539999]')
    [r] = claude.rows('work')
    assert (r.pid, r.where) == (88, 'container')
    # a namespace nobody is in
    put_claude(home, pid=7, pidDomain='linux::pid:[4026530000]')
    assert claude.rows('work') == []
    put_claude(home, pid=7, pidDomain='something else')
    assert claude.rows('work')[0].state == 'not-readable'


def test_claude_where_from_parents(home, proc):
    proc.add(50, ['code'], ppid=1)
    proc.add(51, ['bash'], ppid=50)
    proc.add(4242, ['claude'], ppid=51)
    put_claude(home)
    assert claude.rows('work')[0].where == 'vscode'
    proc.add(60, ['sshd'], ppid=1)
    proc.add(4243, ['claude'], ppid=60)
    put_claude(home, pid=4243, sessionId='77777777-7777-4777-8777-777777777777')
    assert {r.pid: r.where for r in claude.rows('work')}[4243] == 'ssh'


def test_claude_half_written_transcript_line(home, proc):
    proc.add(4242, ['claude'])
    put_claude(home)
    put_transcript(home, text=fixture('claude_transcript.jsonl') + '{"type":"assistant","message":{"con')
    assert claude.rows('work')[0].line.startswith('I added the test')
    big = '{"type":"user","message":{"content":"' + 'x' * 100000 + '"}}\n'
    put_transcript(home, text=fixture('claude_transcript.jsonl') + big)
    assert claude.rows('work')[0].line.startswith('I added the test')
    big = '{"type":"user","message":{"content":"' + 'x' * 300000 + '"}}\n'  # the text is beyond 256 KB
    put_transcript(home, text=fixture('claude_transcript.jsonl') + big)
    assert claude.rows('work')[0].line == ''


def test_claude_no_line(home, proc):
    proc.add(4242, ['claude'])
    put_claude(home)
    put_transcript(home)
    assert claude.rows('work', line=False)[0].line == ''


# ---------------------------------------------------------------- Copilot

def test_copilot_hold_file_and_fields(home, proc):
    proc.add(300, ['copilot'], comm='MainThread')
    d = put_copilot(home, pid=300)
    [r] = copilot.rows('work')
    assert (r.tool, r.id, r.folder, r.state, r.pid, r.version) == ('copilot-cli', CID, '~/code/demo',
                                                                  'needs-reply', 300, '1.0.101')
    assert r.since == sessions.parse_time('2026-10-21T08:00:08Z')
    assert r.paths == (str(d / 'inuse.300.hold'), str(d / 'events.jsonl'))


@pytest.mark.parametrize('tail,state,line', [
    (event('permission.requested', intention='Run the tests'), 'needs-approval', 'Run the tests'),
    (event('permission.requested', intention='x') + event('permission.completed'), 'working', ''),
    (event('assistant.turn_end'), 'needs-reply', ''),
    (event('abort'), 'needs-reply', ''),
    (event('session.error'), 'needs-reply', ''),
    (event('session.resume'), 'needs-reply', ''),
    (event('assistant.turn_end') + event('model.rate_limit') + event('session.info') + event('system.notification'),
     'needs-reply', ''),
    (event('tool.execution_start'), 'working', ''),
    (event('subagent.started'), 'working', ''),
    (event('skill.invoked'), 'working', ''),
    (event('user.message'), 'working', ''),
    (event('session.compaction_start'), 'working', ''),
    (event('session.shutdown'), 'unknown', ''),
    (event('something.new'), 'unknown', ''),
])
def test_copilot_state_mapping(home, proc, tail, state, line):
    proc.add(300, ['copilot'])
    put_copilot(home, pid=300, events=copilot_with_tail(tail))
    [r] = copilot.rows('work')
    assert (r.state, r.line) == (state, line)


def test_copilot_model_event_after_turn_end_and_half_line(home, proc):
    proc.add(300, ['copilot'])
    put_copilot(home, pid=300)  # ends with usage_checkpoint and model.rate_limit after turn_end
    assert copilot.rows('work')[0].state == 'needs-reply'
    put_copilot(home, pid=300, events=copilot_with_tail(event('tool.execution_start')) + '{"type":"assistant.tu')
    assert copilot.rows('work')[0].state == 'working'


def test_copilot_only_bookkeeping_is_unknown(home, proc):
    proc.add(300, ['copilot'])
    put_copilot(home, pid=300, events=event('session.info'))
    [r] = copilot.rows('work')
    assert r.state == 'not-readable'  # no session.start, so no folder


def test_copilot_two_folders_the_newer_log_wins(home, proc):
    proc.add(300, ['copilot'])
    placeholder = put_copilot(home, sid='aaaaaaaa-0000-4000-8000-000000000000', pid=300, events=False)
    old = put_copilot(home, sid='bbbbbbbb-0000-4000-8000-000000000000', pid=300)
    new = put_copilot(home, sid=CID, pid=300)
    os.utime(old / 'events.jsonl', (1000, 1000))
    os.utime(new / 'events.jsonl', (2000, 2000))
    assert placeholder.is_dir()
    assert [r.id for r in copilot.rows('work')] == [CID]


def test_copilot_stale_hold_of_dead_pid_and_foreign_program(home, proc):
    put_copilot(home, pid=301)  # nobody runs pid 301
    proc.add(302, ['node', 'server.js'], comm='MainThread')  # MainThread, but not copilot
    put_copilot(home, sid='cccccccc-0000-4000-8000-000000000000', pid=302)
    assert copilot.rows('work') == []


def test_copilot_resume_without_hold_file(home, proc):
    proc.add(300, ['copilot', '--resume', CID[:13]])
    put_copilot(home)
    [r] = copilot.rows('work')
    assert (r.id, r.pid) == (CID, 300)
    (proc.root / '300' / 'cmdline').write_bytes(f'copilot\0--resume={CID}\0'.encode())
    assert [r.id for r in copilot.rows('work')] == [CID]
    (proc.root / '300' / 'cmdline').write_bytes(b'copilot\0--resume\0')  # Copilot's own picker
    assert copilot.rows('work') == []
    (proc.root / '300' / 'cmdline').write_bytes(b'copilot\0--resume\0--model\0x\0')
    assert copilot.rows('work') == []
    (proc.root / '300' / 'cmdline').write_bytes(b'copilot\0--resume\0../..\0')
    assert copilot.rows('work') == []


def test_copilot_resume_prefix_that_is_ambiguous_gives_no_row(home, proc):
    proc.add(300, ['copilot', '--resume', '2222'])
    put_copilot(home, sid='22220000-0000-4000-8000-000000000000')
    put_copilot(home, sid='22221111-0000-4000-8000-000000000000')
    assert copilot.rows('work') == []


def test_copilot_approval_line_is_redacted_and_cleaned(home, proc):
    proc.add(300, ['copilot'])
    put_copilot(home, pid=300, events=copilot_with_tail(
        event('permission.requested', intention=f'push with {TOKEN}\x1b[31m now\nplease')))
    [r] = copilot.rows('work')
    assert TOKEN not in r.line and '[REDACTED:github-token]' in r.line
    assert '\x1b' not in r.line and '\n' not in r.line


def test_copilot_no_line(home, proc):
    proc.add(300, ['copilot'])
    put_copilot(home, pid=300, events=copilot_with_tail(event('permission.requested', intention='Run it')))
    assert copilot.rows('work', line=False)[0].line == ''


def test_copilot_unsafe_folder_name_is_not_readable(home, proc):
    proc.add(300, ['copilot'])
    put_copilot(home, sid='odd name', pid=300)
    [r] = copilot.rows('work')
    assert r.state == 'not-readable' and r.id == ''


# ---------------------------------------------------------------- opencode

class FakeOpencode:
    def __init__(self, path, answers):
        self.path = path
        self.log = path.parent / 'opencode.calls'
        self.answers = path.parent / 'opencode.json'
        self.answers.write_text(json.dumps(answers))
        path.write_text(
            f'#!{sys.executable}\n'
            'import json, sys\n'
            f'open({str(self.log)!r}, "a").write(json.dumps(sys.argv[1:]) + "\\n")\n'
            f'answers = json.load(open({str(self.answers)!r}))\n'
            'if sys.argv[1:] == ["--version"]:\n'
            '    print("2.0.30"); sys.exit(0)\n'
            'key = " ".join(sys.argv[2:])\n'
            'if key not in answers:\n'
            '    sys.stderr.write("unknown " + key); sys.exit(2)\n'
            'a = answers[key]\n'
            'if isinstance(a, dict) and "exit" in a:\n'
            '    sys.exit(a["exit"])\n'
            'print(json.dumps(a))\n')
        path.chmod(0o755)

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


def oc_answers(**extra):
    get = json.loads(fixture('opencode_get.json'))
    answers = {'session.active': json.loads(fixture('opencode_active.json')),
               'session.get --param sessionID=ses_active1': get,
               'session.permission.list --param sessionID=ses_active1': {'data': []}}
    answers.update(extra)
    return answers


def test_opencode_absent(tools_dir):
    assert opencode.rows('work') == []


def test_opencode_active_session(tools_dir, proc):
    fake = FakeOpencode(tools_dir / 'opencode', oc_answers())
    [r] = opencode.rows('work')
    assert (r.tool, r.id, r.name, r.folder, r.state, r.pid) == ('opencode', 'ses_active1', 'demo work',
                                                              '~/code/demo', 'working', None)
    assert r.since == 1790000050 and r.version == '2.0.30' and r.where == '' and r.error == ''
    assert ['api', 'session.active'] in fake.calls()


def test_opencode_client_with_session_flag_is_idle(tools_dir, proc):
    answers = oc_answers(**{'session.active': {'data': {}},
                            'session.get --param sessionID=ses_given':
                                json.loads(fixture('opencode_get.json')),
                            'session.permission.list --param sessionID=ses_given': {'data': []}})
    FakeOpencode(tools_dir / 'opencode', answers)
    proc.add(400, ['opencode', '-s', 'ses_given'], comm='opencode')
    proc.add(401, ['opencode', 'serve', '--service'], comm='opencode')  # the server is not a client
    proc.add(40, ['code'])
    os.utime(proc.root / '400', None)
    (proc.root / '400' / 'stat').write_text('400 (opencode) S 40 ' + ' '.join(['0'] * 17) + ' 5000 0 0\n')
    [r] = opencode.rows('work')
    assert (r.id, r.state, r.where, r.pid) == ('ses_given', 'needs-reply', 'vscode', None)


def test_opencode_client_found_by_folder_takes_newest(tools_dir, proc, tmp_path):
    folder = tmp_path / 'proj'
    folder.mkdir()
    lst = json.loads(fixture('opencode_list.json'))
    for s in lst['data']:
        s['location']['directory'] = str(folder)
    get = {'data': {'title': 'newer', 'location': {'directory': str(folder)}, 'time': {'updated': 1790000000000}}}
    FakeOpencode(tools_dir / 'opencode', oc_answers(**{
        'session.active': {'data': {}}, 'session.list': lst, 'session.get --param sessionID=ses_new': get,
        'session.permission.list --param sessionID=ses_new': {'data': []}}))
    proc.add(400, ['opencode'], comm='opencode', cwd=str(folder))
    [r] = opencode.rows('work')
    assert (r.id, r.name, r.since, r.state) == ('ses_new', 'newer', 1790000000, 'needs-reply')


def test_opencode_permission_request(tools_dir, proc):
    FakeOpencode(tools_dir / 'opencode', oc_answers(**{
        'session.permission.list --param sessionID=ses_active1': json.loads(fixture('opencode_permission.json'))}))
    [r] = opencode.rows('work')
    assert (r.state, r.line) == ('needs-approval', 'bash rm -rf build')
    assert opencode.rows('work', line=False)[0].line == ''


def test_opencode_failure_is_one_not_readable_row(tools_dir, proc):
    FakeOpencode(tools_dir / 'opencode', {'session.active': {'exit': 1}})
    [r] = opencode.rows('work')
    assert r.state == 'not-readable' and r.error == 'opencode api session.active failed with exit status 1'
    FakeOpencode(tools_dir / 'opencode', oc_answers(**{'session.get --param sessionID=ses_active1': {'exit': 3}}))
    [r] = opencode.rows('work')
    assert r.state == 'not-readable' and r.id == 'ses_active1' and 'session.get failed with exit status 3' in r.error


# ---------------------------------------------------------------- redaction, local-only, once per folder

def test_name_and_folder_are_cleaned_and_name_and_line_redacted(home, proc):
    proc.add(4242, ['claude'])
    put_claude(home, name=f'\x1b[1mdemo {TOKEN}\x1b[0m', cwd='~/code/\x1b[31mdemo\x07\n2')
    put_transcript(home, text=json.dumps({'type': 'assistant', 'message': {'content': [
        {'type': 'text', 'text': f'key {TOKEN}\x1b[2J and a very long tail ' + 'z' * 300}]}}) + '\n')
    [r] = claude.rows('work')
    assert r.name == 'demo [REDACTED:github-token]'
    assert r.folder == '~/code/demo 2'
    assert TOKEN not in r.line and '\x1b' not in r.line and len(r.line) == 120


def test_local_only_folder_has_no_line_and_transcript_is_not_opened(home, proc, store, monkeypatch):
    (home / '.agents' / 'config.toml').write_text('[privacy]\nlocal_only = ["/work/secret"]\n')
    cfg.configure(store)
    proc.add(4242, ['claude'])
    put_claude(home, cwd='/work/secret/app')
    path = put_transcript(home)
    real_open = builtins.open

    def guard(file, *a, **k):
        if str(file) == str(path):
            raise AssertionError('the transcript of a local-only folder was opened')
        return real_open(file, *a, **k)
    monkeypatch.setattr(builtins, 'open', guard)
    [r] = claude.rows('work')
    assert (r.local_only, r.line, r.state) == (True, '', 'needs-reply')


def test_local_only_copilot_approval_has_no_line(home, proc, store):
    (home / '.agents' / 'config.toml').write_text('[privacy]\nlocal_only = ["/work/secret"]\n')
    cfg.configure(store)
    proc.add(300, ['copilot'])
    put_copilot(home, pid=300, cwd='/work/secret', events=copilot_with_tail(
        event('permission.requested', intention='Run it')).replace('~/code/demo', '/work/secret'))
    [r] = copilot.rows('work')
    assert (r.local_only, r.line, r.state) == (True, '', 'needs-approval')


def test_is_local_project_asked_once_per_folder(home, proc, monkeypatch):
    asked = []
    monkeypatch.setattr(cfg, 'is_local_project', lambda folder: asked.append(folder) or False)
    for pid in (20, 21):
        proc.add(pid, ['claude'])
        put_claude(home, pid=pid, sessionId=f'88888888-8888-4888-8888-8888888888{pid}')
    proc.add(300, ['copilot'])
    put_copilot(home, pid=300)
    assert len(sessions.local_rows()) == 3
    assert asked == ['~/code/demo']


def test_no_line_reads_no_transcript(home, proc, monkeypatch):
    proc.add(4242, ['claude'])
    put_claude(home)
    path = put_transcript(home)
    real_open = builtins.open

    def guard(file, *a, **k):
        if str(file) == str(path):
            raise AssertionError('opened')
        return real_open(file, *a, **k)
    monkeypatch.setattr(builtins, 'open', guard)
    assert sessions.local_rows(line=False)[0].line == ''


def test_local_rows_sorted_and_adapter_that_raises(home, proc, monkeypatch):
    proc.add(20, ['claude'])
    put_claude(home, pid=20, status='busy', sessionId='99999999-9999-4999-8999-999999999999')
    proc.add(300, ['copilot'])
    put_copilot(home, pid=300)
    assert [r.state for r in sessions.local_rows()] == ['needs-reply', 'working']
    monkeypatch.setattr(copilot, 'rows', lambda machine, line=True: 1 / 0)
    rows = sessions.local_rows()
    assert [r.state for r in rows] == ['working', 'not-readable']


# ---------------------------------------------------------------- the command

@pytest.fixture
def one_row(home, proc):
    proc.add(4242, ['claude'])
    put_claude(home, statusUpdatedAt=int((time.time() - 250) * 1000), name='demo')
    put_transcript(home)
    return home


def run(args, capsys):
    code = cli.main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_resume_json_shape(one_row, store, capsys):
    code, out, err = run(['resume', '--store', store, '--json'], capsys)
    data = json.loads(out)
    assert code == 0 and data['ok'] is True and data['command'] == 'resume'
    assert data['format'] == 'llmpro-sessions.v1' and data['machine'] == cfg.MACHINE and data['errors'] == []
    [row] = data['rows']
    assert sessions.Row.from_dict(row).id == SID and row['state'] == 'needs-reply'
    assert 'paths' not in row


def test_resume_json_with_not_readable_row_exits_0_and_no_line(one_row, store, capsys):
    put_claude(one_row, pid=77, cwd=None)
    (one_row / '.claude/sessions/77.json').write_text(json.dumps({'pid': 4242, 'sessionId': 'x y'}))
    code, out, _ = run(['resume', '--store', store, '--json', '--no-line'], capsys)
    rows = json.loads(out)['rows']
    assert code == 0 and {r['state'] for r in rows} == {'needs-reply', 'not-readable'}
    assert all(r['line'] == '' for r in rows)


def test_resume_human_output_piped_has_no_color(one_row, store, capsys):
    code, out, err = run(['resume', '--store', store], capsys)
    lines = out.splitlines()
    assert code == 0 and len(lines) == 2 and '\x1b' not in out
    assert lines[0].split()[:3] == ['#', 'STATE', 'TOOL']
    cells = lines[1].split()
    assert cells[:4] == ['1', 'needs-reply', 'claude-code', f'demo@{cfg.MACHINE}']
    assert '~/code/demo' in lines[1] and '4m' in cells and 'I added the test' in lines[1]


def test_resume_tilde_and_ages():
    assert sessions.tilde(cfg.home() + '/code/x') == '~/code/x' and sessions.tilde('/etc') == '/etc'
    assert [sessions.age(100 - d, 100) for d in (30, 240, 7200, 3 * 86400)] == ['30s', '4m', '2h', '3d']
    assert sessions.age(None) == '-'


def test_resume_color_only_on_a_terminal_without_no_color(one_row, store, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True, raising=False)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False, raising=False)
    monkeypatch.delenv('NO_COLOR', raising=False)
    # capsys replaces sys.stdout, so patch the object it gave us
    out = sys.stdout
    monkeypatch.setattr(out, 'isatty', lambda: True, raising=False)
    assert sessions.use_color()
    monkeypatch.setenv('NO_COLOR', '1')
    assert not sessions.use_color()
    code, text, _ = run(['resume', '--store', store], capsys)
    assert code == 0 and '\x1b' not in text


def test_resume_no_rows(home, proc, store, capsys):
    code, out, err = run(['resume', '--store', store], capsys)
    assert (code, out, err) == (0, '', 'no open sessions\n')


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_resume_prompt_enter_alone_and_an_answer(one_row, store, capsys, monkeypatch):
    monkeypatch.setattr(sys, 'stdin', Tty(''))
    monkeypatch.setattr(sys, 'stdout', Tty())
    asked = []
    monkeypatch.setattr(builtins, 'input', lambda prompt='': asked.append(prompt) or '')
    assert cli.main(['resume', '--store', store]) == 0
    assert asked == ['Continue which? (number or name, Enter to quit) ']
    monkeypatch.setattr(builtins, 'input', lambda prompt='': '1')
    assert cli.main(['resume', '--store', store]) == 2
    assert 'continuing a session comes in a later version' in capsys.readouterr().err
    asked.clear()
    assert cli.main(['resume', '--store', store, '-n']) == 0 and asked == []


def test_resume_later_forms(one_row, store, capsys):
    for args, form in ((['x'], 'a target'), (['--watch'], '--watch'), (['--machine', 'other.example.com'], '--machine')):
        code, out, err = run(['resume', '--store', store] + args, capsys)
        assert (code, out) == (2, '')
        assert err == f'llmpro resume: {form} comes in a later version\n'
    assert run(['resume', '--store', store, '--local', '--json'], capsys)[0] == 0
    assert run(['resume', '--store', store, '--machine', cfg.MACHINE, '--json'], capsys)[0] == 0


def test_resume_needs_linux(one_row, store, capsys, monkeypatch):
    monkeypatch.setattr(sessions, 'supported', lambda: False)
    assert run(['resume', '--store', store], capsys) == (2, '', 'live sessions need Linux\n')


def test_resume_capabilities():
    assert 'resume' in cli.CAPABILITIES and 'resume.json' in cli.CAPABILITIES


def test_listing_twenty_records_is_quick_and_imports_no_importer(home, proc):
    for i in range(20):
        proc.add(1000 + i, ['claude'])
        put_claude(home, pid=1000 + i, sessionId=f'aaaaaaaa-0000-4000-8000-{i:012d}')
    for i in range(200):
        proc.add(2000 + i, ['sleep', '1'])
    t = time.monotonic()
    rows = sessions.local_rows()
    assert len(rows) == 20 and time.monotonic() - t < 1


def test_listing_imports_no_importer_and_no_plugin():
    src = os.path.dirname(os.path.dirname(sessions.__file__))
    code = ('import sys; sys.path.insert(0, sys.argv[1]); from llmpro import sessions; '
            'from llmpro.sessions import claude, copilot, opencode; '
            'print([m for m in sys.modules if m.startswith(("llmpro.importer", "llmpro.plugins", "llmpro.sync"))])')
    out = subprocess.run([sys.executable, '-c', code, src], capture_output=True, text=True, cwd='/')
    assert out.stdout.strip() == '[]', out.stderr
