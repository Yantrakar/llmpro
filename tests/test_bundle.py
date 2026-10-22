"""The bundle: the skill, the Claude Code plugin files, the agent-check hook and the skill setup item.
Everything runs offline, in a temporary HOME, with a fake gitpro."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

import llmpro
from llmpro import cli

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / 'skills' / 'llmpro'
MARKER = '<!-- installed by llmpro setup; llmpro setup updates it, edits here are overwritten -->'
HOOK_COMMAND = "sh -c 'command -v llmpro >/dev/null 2>&1 || exit 0; llmpro hook agent-check; exit 0'"
EVENT = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'git push'}})
REPLY = json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow'}})
ARGV = ['agent-check', '--agent', 'claude']
HEAVY = ('argparse', 'importlib.metadata', 'llmpro.plugins.memory', 'llmpro.plugins.secrets', 'llmpro.plugins.git')


def front_matter(text):
    """-> ({key: value}, body). A small parser for the simple front matter of a SKILL.md."""
    lines = text.split('\n')
    assert lines[0] == '---', 'no front matter'
    end = lines.index('---', 1)
    fields = {}
    for line in lines[1:end]:
        key, _, value = line.partition(':')
        fields[key.strip()] = value.strip().strip('"')
    return fields, '\n'.join(lines[end + 1:])


# ---------------------------------------------------------------- the skill

def test_skill_front_matter_and_body():
    fields, body = front_matter((SKILL / 'SKILL.md').read_text())
    assert fields['name'] == 'llmpro'
    assert fields['license'] == 'MIT'
    assert 0 < len(fields['description']) <= 1024
    assert len(body.strip().splitlines()) < 150


def test_skill_references_exist_and_are_linked():
    _, body = front_matter((SKILL / 'SKILL.md').read_text())
    links = set(re.findall(r'\]\((references/[\w.-]+\.md)\)', body))
    assert links == {'references/memory.md', 'references/secrets.md', 'references/git.md'}
    for link in links:
        assert (SKILL / link).is_file()


def test_docs_have_no_em_dash_or_machine_path():
    for path in [ROOT / 'README.md', *SKILL.rglob('*.md'), ROOT / 'src/llmpro/templates/AGENTS.md']:
        text = path.read_text()
        assert '—' not in text, path
        assert '/home/' not in text, path


def test_readme_is_short():
    assert len((ROOT / 'README.md').read_text().splitlines()) < 200


# ---------------------------------------------------------------- the Claude Code plugin

def load(*parts):
    return json.loads(ROOT.joinpath(*parts).read_text())


def test_plugin_json():
    plugin = load('.claude-plugin', 'plugin.json')
    assert plugin['name'] == 'llmpro'
    assert 'version' not in plugin  # Claude Code then uses the commit of the plugin folder
    market = load('.claude-plugin', 'marketplace.json')
    assert 'version' not in market and all('version' not in p for p in market['plugins'])
    assert plugin['description']
    assert plugin['author'] == {'name': 'Yantrakar'}
    assert plugin['repository'] == 'https://github.com/Yantrakar/llmpro'
    assert plugin['license'] == 'MIT'


def test_marketplace_json():
    market = load('.claude-plugin', 'marketplace.json')
    assert market['name'] == 'llmpro'
    assert [(p['name'], p['source']) for p in market['plugins']] == [('llmpro', './')]


def test_mcp_json():
    assert load('.mcp.json') == {'mcpServers': {'llmpro': {'type': 'stdio', 'command': 'llmpro', 'args': ['mcp']}}}


def test_hooks_json():
    hooks = load('hooks', 'hooks.json')['hooks']['PreToolUse']
    assert len(hooks) == 1 and hooks[0]['matcher'] == 'Bash'
    assert hooks[0]['hooks'] == [{'type': 'command', 'command': HOOK_COMMAND, 'timeout': 10}]


# ---------------------------------------------------------------- the agent hook

CHILD = '''
import json, os, sys
from llmpro import hot
code = hot.main(["hook", "agent-check"])
sys.stdout.flush()
with open(os.environ["MODULES_OUT"], "w") as fh:
    json.dump(sorted(sys.modules), fh)
sys.exit(code)
'''


@pytest.fixture
def hook(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('HOME', str(tmp_path))
    out = tmp_path / 'modules.json'
    monkeypatch.setenv('MODULES_OUT', str(out))

    def run(stdin=EVENT, timeout=30):
        out.unlink(missing_ok=True)
        r = subprocess.run([sys.executable, '-c', CHILD], input=stdin, capture_output=True, text=True,
                           timeout=timeout)
        return r, json.loads(out.read_text()) if out.exists() else []
    return run


def versions(fg):
    return [c for c in fg.calls() if c == ['--version', '--json']]


def test_hook_without_gitpro_is_silent_and_lean(hook):
    r, mods = hook()
    assert (r.returncode, r.stdout, r.stderr) == (0, '', '')
    assert mods
    for bad in HEAVY + ('llmpro.setup',):
        assert bad not in mods, bad


def test_hook_with_gitpro_stays_lean_too(hook, fake_gitpro):
    fake_gitpro.answer(ARGV, REPLY)
    r, mods = hook()
    assert r.stdout.strip() == REPLY
    for bad in HEAVY:
        assert bad not in mods, bad


def test_hook_passes_the_reply_and_asks_the_version_once(hook, fake_gitpro):
    fake_gitpro.answer(ARGV, REPLY)
    r, _ = hook()
    assert (r.returncode, r.stdout.strip()) == (0, REPLY)
    assert len(versions(fake_gitpro)) == 1
    assert fake_gitpro.calls().count(ARGV) == 1
    r, _ = hook()
    assert r.stdout.strip() == REPLY
    assert len(versions(fake_gitpro)) == 1  # the cache answered
    assert fake_gitpro.calls().count(ARGV) == 2
    later = time.time() + 100
    os.utime(fake_gitpro.path, (later, later))
    hook()
    assert len(versions(fake_gitpro)) == 2  # the program changed: asked again


def test_hook_gives_gitpro_the_same_input(hook, fake_gitpro):
    # the fake does not record stdin, so use a program that does
    body = ('#!%s\nimport sys, json, os\nhere = os.path.dirname(os.path.abspath(__file__))\n'
            'if sys.argv[1:] == ["--version", "--json"]:\n'
            '    print(json.dumps({"ok": True, "version": "0.4.0", "capabilities": ["agent-check"]}))\n'
            'else:\n'
            '    open(os.path.join(here, "stdin.txt"), "w").write(sys.stdin.read())\n'
            '    print("{}")\n') % sys.executable
    Path(fake_gitpro.path).write_text(body)
    r, _ = hook(stdin=EVENT)
    assert r.returncode == 0 and r.stdout.strip() == '{}'
    assert (Path(fake_gitpro.path).parent / 'stdin.txt').read_text() == EVENT


def test_hook_passes_gitpros_exit_code(hook, fake_gitpro):
    fake_gitpro.answer(ARGV, '{"decision": "block"}', exit=2)
    r, _ = hook()
    assert r.returncode == 2 and json.loads(r.stdout) == {'decision': 'block'}


def test_hook_does_nothing_without_the_capability(hook, fake_gitpro):
    fake_gitpro.version(capabilities=('why', 'setup'))
    r, _ = hook()
    assert (r.returncode, r.stdout) == (0, '')
    assert ARGV not in fake_gitpro.calls()


def test_hook_does_nothing_with_an_old_gitpro(hook, fake_gitpro):
    fake_gitpro.version(v='0.3.2', capabilities=('agent-check',))
    r, _ = hook()
    assert (r.returncode, r.stdout) == (0, '')
    assert ARGV not in fake_gitpro.calls()


def test_hook_survives_a_broken_cache(hook, fake_gitpro, tmp_path):
    fake_gitpro.answer(ARGV, REPLY)
    hook()
    cache = tmp_path / 'state' / 'llmpro' / 'gitpro-version.json'
    assert cache.is_file()
    for junk in ('not json{', '[1, 2]', '{"path": 1}'):
        cache.write_text(junk)
        r, _ = hook()
        assert (r.returncode, r.stdout.strip()) == (0, REPLY)


def test_hook_survives_a_gitpro_that_cannot_run(hook, fake_gitpro):
    Path(fake_gitpro.path).write_text('#!/nonexistent/interpreter\n')
    r, _ = hook()
    assert (r.returncode, r.stdout) == (0, '')


def test_hook_asks_again_after_a_version_that_did_not_answer(hook, fake_gitpro):
    fake_gitpro.answer(ARGV, REPLY)
    fake_gitpro.version_raw('not json')
    r, _ = hook()
    assert (r.returncode, r.stdout) == (0, '')
    fake_gitpro.version()  # the program file is the same; only its answer changed
    r, _ = hook()
    assert r.stdout.strip() == REPLY
    assert len(versions(fake_gitpro)) == 2


def test_hook_passes_gitpros_stderr(hook, fake_gitpro):
    body = ('#!%s\nimport sys, json\n'
            'if sys.argv[1:] == ["--version", "--json"]:\n'
            '    print(json.dumps({"ok": True, "version": "0.4.0", "capabilities": ["agent-check"]}))\n'
            'else:\n'
            '    print("{}"); print("gitpro: why", file=sys.stderr)\n') % sys.executable
    Path(fake_gitpro.path).write_text(body)
    r, _ = hook()
    assert (r.returncode, r.stdout.strip(), r.stderr.strip()) == (0, '{}', 'gitpro: why')


def test_hook_gives_up_on_a_slow_gitpro(hook, fake_gitpro):
    fake_gitpro.answer(ARGV, REPLY, sleep=20)
    start = time.time()
    r, _ = hook(timeout=15)
    assert (r.returncode, r.stdout) == (0, '')
    assert time.time() - start < 10


# ---------------------------------------------------------------- the skill in the store

def run(argv, capsys):
    code = cli.main(argv)
    c = capsys.readouterr()
    return code, c.out, c.err


def skill_item(machine, capsys):
    code, out, _ = run(['--store', str(machine / '.agents'), 'setup', '-n', '--json', '--only', 'memory'], capsys)
    return code, {i['name']: i for i in json.loads(out)['items']}['skill: llmpro']


def undo_of(out):
    return [l for l in out.splitlines() if l.startswith('To undo: sh ')][0][len('To undo: sh '):]


def test_skill_item_is_missing_in_a_fresh_store(machine, store, capsys):
    code, item = skill_item(machine, capsys)
    assert code == 1 and not item['present']


def test_setup_writes_the_skill_and_undo_removes_it(machine, store, capsys):
    code, out, err = run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)
    assert code == 0, err
    dest = Path(store) / 'skills' / 'llmpro'
    fields, body = front_matter((dest / 'SKILL.md').read_text())
    assert fields['name'] == 'llmpro'
    assert body.lstrip('\n').startswith(MARKER)
    for ref in ('memory', 'secrets', 'git'):
        assert (dest / 'references' / f'{ref}.md').read_text() == (SKILL / 'references' / f'{ref}.md').read_text()
    assert skill_item(machine, capsys)[1]['present']
    subprocess.run(['sh', undo_of(out)], check=True, capture_output=True)
    assert not dest.exists()


def test_older_copy_with_the_marker_is_updated(machine, store, capsys):
    run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)
    dest = Path(store) / 'skills' / 'llmpro'
    new = (dest / 'SKILL.md').read_text()
    (dest / 'SKILL.md').write_text(new + '\nan older line\n')
    code, item = skill_item(machine, capsys)
    assert code == 1 and not item['present']
    code, out, err = run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)
    assert code == 0, err
    assert (dest / 'SKILL.md').read_text() == new
    subprocess.run(['sh', undo_of(out)], check=True, capture_output=True)
    assert 'an older line' in (dest / 'SKILL.md').read_text()  # the undo put the earlier files back


def test_copy_without_the_marker_is_left_alone(machine, store, capsys):
    dest = Path(store) / 'skills' / 'llmpro'
    dest.mkdir(parents=True)
    mine = '---\nname: llmpro\ndescription: mine\n---\nmy own text\n'
    (dest / 'SKILL.md').write_text(mine)
    code, item = skill_item(machine, capsys)
    assert item['present']
    assert 'edited by hand; llmpro setup leaves it' in item['details']
    assert run(['--store', store, 'setup', '-y', '--only', 'memory'], capsys)[0] == 0
    assert (dest / 'SKILL.md').read_text() == mine


# ---------------------------------------------------------------- the wheel

def test_smoke_wheel_checks_the_skill():
    assert 'llmpro/skills/llmpro/SKILL.md' in (ROOT / 'tests' / 'smoke_wheel.py').read_text()
