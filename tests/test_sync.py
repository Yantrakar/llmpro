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
