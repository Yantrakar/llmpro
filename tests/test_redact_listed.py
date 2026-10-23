"""HTTP Basic credentials and the store's blocklist of fingerprints, in redact() and in part 1 of the check
(spec 24). Made-up values, built at run time, of the shapes found on 2026-10-23."""
import base64
import hashlib
import json
import os

import pytest

from llmpro import check, cli, redact
from llmpro.importer import all as importer

A = ''.join(['aB%c', 'D3eF', '%gHi', 'JkLm'])  # 16 characters, letters, digits and two %, no shape a rule finds
B = base64.b64encode(''.join(['someone@mail.example', ':', 'Kq7vR2mZ', 'p9Lw4xTn']).encode()).decode()
LINE = json.dumps(['.iot.example.tech', '13267', A, 'App/1.0 (iPhone; iOS 18.4.1; Scale/3.00)', f'Basic {B}',
                   '000000', 'status', 'masterUid'])


def fp(v):
    return hashlib.sha256(v.encode()).hexdigest()[:16]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(redact, 'LEARNED', {})
    monkeypatch.setattr(redact, 'LEARNED_BY', {})
    monkeypatch.setattr(redact, 'BLOCKED', set())
    monkeypatch.setattr(check, 'BLOCKED', set())


def test_basic_credential_is_masked_and_found():
    out = redact.redact(LINE)
    assert B not in out and f'"Basic [REDACTED:auth-header]"' in out
    assert A in out  # not listed: left as it is
    assert [(k, v) for k, v, _ in check.scan_more(LINE)] == [('auth-header', B)]
    assert B in redact.LEARNED and 'Kq7vR2mZp9Lw4xTn' in redact.LEARNED  # the password inside it too


@pytest.mark.parametrize('text', [
    'a basic configuration of the server',
    'Basic ' + base64.b64encode(bytes(range(200, 230))).decode(),  # not text
    'Basic ' + base64.b64encode(b'no colon in this text at all').decode(),
    'Basic ' + base64.b64encode(b':nothing-before-it').decode(),
    'Basic ' + base64.b64encode(b'nothing-after-it:').decode(),
])
def test_basic_leaves_what_is_no_credential(text):
    assert redact.redact(text) == text
    assert check.scan_more(text) == []


@pytest.mark.parametrize('text', [LINE, f'"x={A}&y=1"', f'`{A}`', json.dumps(json.dumps([A])), f'{A}.', f'{A}:'])
def test_a_listed_value_is_masked_and_found(text, monkeypatch):
    redact.BLOCKED.add(fp(A))
    check.BLOCKED.add(fp(A))
    out = redact.redact(text)
    assert A not in out and '[REDACTED:listed]' in out
    assert ('listed', A) in [(k, v) for k, v, _ in check.scan_more(text)]


def test_a_listed_value_glued_in_a_run_is_swept():
    redact.BLOCKED.add(fp(A))
    redact.redact(f'"{A}"')
    assert A in redact.LEARNED
    assert A not in redact.sweep(f'glued{A}glued')


def test_an_unlisted_value_is_left_alone():
    redact.BLOCKED.add(fp('something-else-entirely'))
    assert redact.redact(f'"{A}"') == f'"{A}"'
    check.BLOCKED.add(fp('something-else-entirely'))
    assert check.scan_more(f'"{A}"') == []


def test_the_noop_key_follows_the_list():
    assert importer.noop_code() == importer.CODE
    redact.BLOCKED.add(fp(A))
    one = importer.noop_code()
    assert one != importer.CODE
    redact.BLOCKED.add(fp('another-listed-value'))
    assert importer.noop_code() not in (one, importer.CODE)


def write_list(store, entries):
    os.makedirs(os.path.join(store, '.agentspro'), exist_ok=True)
    with open(os.path.join(store, '.agentspro', 'blocklist.json'), 'w') as fh:
        json.dump(entries, fh)


def test_the_list_is_read_from_the_store(store):
    assert redact.load_blocklist(store) == set() == check.load_block(store)
    write_list(store, {fp(A): 'the app value (made up)'})
    assert redact.load_blocklist(store) == {fp(A)} == check.load_block(store)
    write_list(store, ['not', 'a', 'map'])
    with pytest.raises(ValueError):
        redact.load_blocklist(store)
    with pytest.raises(ValueError):
        check.load_block(store)


def test_check_fails_on_a_listed_value_and_on_basic(store, capsys):
    os.makedirs(os.path.join(store, 'history'))
    with open(os.path.join(store, 'history', 'chat.md'), 'w') as fh:
        fh.write(f'one\n{json.dumps([A])}\n')
    assert cli.main(['check', '--store', store, '--part', '1']) == 0
    capsys.readouterr()
    write_list(store, {fp(A): 'the app value (made up)'})
    assert cli.main(['check', '--store', store, '--part', '1']) == 1
    out = capsys.readouterr().out
    assert 'PATTERN history/chat.md:2 [listed]' in out and A not in out
    with open(os.path.join(store, 'history', 'chat.md'), 'w') as fh:
        fh.write(f'"Basic {B}"\n')
    assert cli.main(['check', '--store', store, '--part', '1']) == 1
    out = capsys.readouterr().out
    assert 'PATTERN history/chat.md:1 [auth-header]' in out and B not in out


def test_an_allowlisted_listed_value_passes(store, capsys):
    os.makedirs(os.path.join(store, 'history'))
    with open(os.path.join(store, 'history', 'chat.md'), 'w') as fh:
        fh.write(f'{json.dumps([A])}\n')
    write_list(store, {fp(A): 'the app value (made up)'})
    with open(os.path.join(store, '.agentspro', 'allowlist.json'), 'w') as fh:
        json.dump({fp(A): 'made up, and public after all'}, fh)
    assert cli.main(['check', '--store', store, '--part', '1']) == 0


def test_the_history_cache_follows_the_list():
    before = check.blob_cache_key({})
    check.BLOCKED.add(fp(A))
    assert check.blob_cache_key({}) != before
