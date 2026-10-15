import os
import subprocess

import pytest

from agentspro import store as cfg


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temporary HOME, so nothing reads or writes the real one."""
    h = tmp_path / 'home'
    h.mkdir()
    monkeypatch.setenv('HOME', str(h))
    monkeypatch.delenv('AGENTS_STORE', raising=False)
    return h


@pytest.fixture
def store(home):
    """A temporary store: a git repo with an empty config.toml and the settings sync would make."""
    d = home / '.agents'
    d.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(d)], check=True)
    for k, v in (('user.name', 'test'), ('user.email', 'test@example.com')):
        subprocess.run(['git', '-C', str(d), 'config', k, v], check=True)
    (d / 'config.toml').write_text('[store]\nformat = 1\nredact_version = 1\n\n[privacy]\nlocal_only = []\n')
    (d / '.gitignore').write_text('sessions/\nlocal/\n')
    yield str(d)
    cfg.LOCAL_ONLY[:] = []
