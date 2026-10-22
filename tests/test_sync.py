def test_notify_finds_notify_send_through_tools_which(tmp_path, monkeypatch):
    """Tests hide outside programs; a failed sync in a test must not pop up a real desktop notice."""
    from llmpro import sync
    log = tmp_path / 'log'
    fake = tmp_path / 'bin' / 'notify-send'
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {log}\n')
    fake.chmod(0o755)
    sync.notify('boom')
    assert not log.exists()
    monkeypatch.setenv('LLMPRO_TOOL_PATH', str(fake.parent))
    sync.notify('boom')
    args = log.read_text().splitlines()
    assert args[:3] == ['--app-name=llmpro', '--icon=dialog-warning', 'llmpro sync failed'] and args[3:] == ['boom']


# ---- the no-op cache (spec 23)

def test_noop_cache_is_keyed_on_the_redaction_code(tmp_path, monkeypatch):
    from llmpro import REDACT_VERSION, __version__, redact
    from llmpro.importer import all as importer
    assert importer.CODE == importer.code_key()
    assert importer.CODE.startswith(f'redact{REDACT_VERSION}+') and __version__ not in importer.CODE
    text = open(redact.__file__, encoding='utf-8').read()
    a = text.index('\n# ---------------------------------------------------------------- redaction\n')
    b = text.index('\n# ---------------------------------------------------------------- text helpers\n')
    copy = tmp_path / 'redact.py'
    monkeypatch.setattr(importer.common, '__file__', str(copy))
    copy.write_text(text[:a] + '\n# a comment elsewhere' + text[a:])
    assert importer.code_key() == importer.CODE  # other code changed: the cache stands
    copy.write_text(text[:b] + '\n# a change in the redaction' + text[b:])
    assert importer.code_key() != importer.CODE  # the redaction changed: the cache goes
    copy.write_text('no markers here\n')
    assert importer.code_key() == f'redact{REDACT_VERSION}+{__version__}'
    copy.unlink()
    assert importer.code_key() == f'redact{REDACT_VERSION}+{__version__}'


def test_noop_cache_is_private(tmp_path):
    import os
    import stat
    from llmpro.importer import all as importer
    store = tmp_path / 'store'
    (store / '.git' / 'agentspro').mkdir(parents=True)
    path = store / importer.NOOP_CACHE
    path.write_text('{}')
    path.chmod(0o664)
    importer.save_noop(str(store), {'h1', 'h2'})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert importer.load_noop(str(store)) == {'h1', 'h2'}
    os.unlink(path)
    importer.save_noop(str(store), {'h3'})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert importer.load_noop(str(store)) == {'h3'}
