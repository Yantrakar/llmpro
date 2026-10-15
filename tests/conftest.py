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


@pytest.fixture
def onpath(monkeypatch):
    """Hooks and the merge driver run `agentspro`: put the one that belongs to this Python on PATH."""
    import sys
    monkeypatch.setenv('PATH', os.path.dirname(sys.executable) + os.pathsep + os.environ['PATH'])


@pytest.fixture
def machine(home, monkeypatch):
    """A temporary HOME with fake tool folders, and no tool running."""
    from agentspro import setup
    monkeypatch.setattr(setup, 'running_tools', lambda: [])
    monkeypatch.setenv('XDG_STATE_HOME', str(home / 'state'))

    def put(rel, text='x\n'):
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    put('.claude/projects/p1/s1.jsonl', '{"a": 1}\n')
    put('.claude/file-history/f1', 'old\n')
    put('.claude/CLAUDE.md', '# mine\nkeep this')  # no trailing newline
    put('.claude/settings.json', '{"fake": true}\n')
    put('.codex/sessions/2026/r1.jsonl', '{}\n')
    put('.codex/AGENTS.md', 'codex rules\n')
    put('.codex/auth.json', '{"fake": "not a real credential"}\n')
    put('.copilot/session-state/a/events.jsonl', '{}\n')
    put('.local/share/opencode/opencode.db', 'not a database')
    (home / '.config/opencode').mkdir(parents=True)
    return home
