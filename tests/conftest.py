"""Fixtures for the tests. Nothing here reaches the real home or a real gitpro, claude or codex.

  home          a temporary HOME
  store         a temporary store (a git repo with config.toml) under that HOME
  onpath        puts this Python's bin folder on PATH, so hooks find `llmpro`
  machine       a temporary HOME with fake tool folders and XDG_STATE_HOME under it
  tools_dir     (autouse) an empty folder that $LLMPRO_TOOL_PATH points at, so `tools.which` finds nothing
                unless a test puts a fake program there; also sets LLMPRO_GITPRO to a path that does not
                exist and LLMPRO_NO_RENAME_NOTE=1, and resets the gitpro and plugin caches around each test
  fake_gitpro   a fake `gitpro` (tests/fakes/fake_gitpro.py) that LLMPRO_GITPRO points at:
                  fg.version(v='0.4.0', capabilities=(...))   the answer to --version --json
                  fg.answer(argv, stdout, exit=0)             the answer to one exact argv
                  fg.mcp(tools, results={}, protocols=(...))  turns `gitpro mcp` into a stdio MCP server
                  fg.calls()                                  every argv it was run with, in order
                  fg.path                                     the program
"""
import json
import os
import subprocess
import sys

import pytest

from llmpro import store as cfg

FAKE_GITPRO = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fakes', 'fake_gitpro.py')
GITPRO_CAPABILITIES = ('setup', 'setup.dry-run', 'why', 'why.config', 'why.commit', 'mcp', 'guard', 'agent-check',
                       'scan.lost', 'env', 'audit', 'report')


@pytest.fixture(autouse=True)
def tools_dir(tmp_path, monkeypatch):
    """An empty folder for fake programs; the only place `tools.which` looks. See the top of this file."""
    from llmpro import gitpro, plugins
    d = tmp_path / 'tools'
    d.mkdir()
    monkeypatch.setenv('LLMPRO_TOOL_PATH', str(d))
    monkeypatch.setenv('LLMPRO_GITPRO', str(tmp_path / 'no-such-gitpro'))
    monkeypatch.setenv('LLMPRO_NO_RENAME_NOTE', '1')
    gitpro.reset_cache()
    plugins.reset_cache()
    yield d
    gitpro.reset_cache()
    plugins.reset_cache()


class FakeGitpro:
    def __init__(self, folder):
        self.path = str(folder / 'gitpro')
        self._json = folder / 'gitpro.json'
        self._log = folder / 'gitpro.log'
        self._data = {}
        self.version()

    def _save(self):
        self._json.write_text(json.dumps(self._data))

    def version(self, v='0.4.0', capabilities=GITPRO_CAPABILITIES):
        self._data['version'] = {'ok': True, 'command': 'version', 'version': v, 'python': '3.11.0',
                                 'capabilities': list(capabilities)}
        self._save()

    def version_raw(self, text):
        """Answer --version --json with this text, which need not be JSON."""
        self._data['version'] = text
        self._save()

    def slow_version(self, seconds):
        self._data['version_sleep'] = seconds
        self._save()

    def answer(self, args, stdout, exit=0, sleep=0):
        self._data.setdefault('answers', []).append({'argv': list(args), 'stdout': stdout, 'exit': exit, 'sleep': sleep})
        self._save()

    def mcp(self, tools, results=None, protocols=('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')):
        self._data['mcp'] = {'tools': tools, 'results': results or {}, 'protocols': list(protocols)}
        self._save()

    def calls(self):
        if not self._log.exists():
            return []
        return [json.loads(line) for line in self._log.read_text().splitlines()]


@pytest.fixture
def fake_gitpro(tmp_path, monkeypatch):
    """A fake gitpro that answers like gitpro 0.4.0. See the top of this file."""
    folder = tmp_path / 'fakebin'
    folder.mkdir()
    body = open(FAKE_GITPRO, encoding='utf-8').read().split('\n', 1)[1]
    target = folder / 'gitpro'
    target.write_text(f'#!{sys.executable}\n{body}')
    target.chmod(0o755)
    monkeypatch.setenv('LLMPRO_GITPRO', str(target))
    from llmpro import gitpro
    gitpro.reset_cache()
    return FakeGitpro(folder)


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
    """Hooks and the merge driver run `llmpro`: put the one that belongs to this Python on PATH."""
    import sys
    monkeypatch.setenv('PATH', os.path.dirname(sys.executable) + os.pathsep + os.environ['PATH'])


@pytest.fixture
def machine(home, monkeypatch):
    """A temporary HOME with fake tool folders, and no tool running."""
    from llmpro import setup
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
    put('.local/state/opencode/prompt-history.jsonl', '{}\n')
    (home / '.config/opencode').mkdir(parents=True)
    return home
