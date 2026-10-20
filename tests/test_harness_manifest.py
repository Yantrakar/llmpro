"""The [harness] section of config.toml, and what `setup -n` reports about it."""
import json
import os
import subprocess

import pytest

from llmpro import cli, setup, store as cfg

DEFAULTS = [('instructions', 'AGENTS.md'), ('memory', 'memory/'), ('skills', 'skills/'), ('subagents', 'agents/'),
            ('hooks', 'hooks/'), ('guardrails', 'guardrails/'), ('scripts', 'scripts/')]


def run(argv, capsys):
    code = cli.main(argv)
    c = capsys.readouterr()
    return code, c.out, c.err


def set_config(store, text):
    with open(os.path.join(store, 'config.toml'), 'a', encoding='utf-8') as fh:
        fh.write(text)


def line(out, part):
    found = [l.strip() for l in out.splitlines() if l.strip().startswith(part + ':')]
    assert len(found) == 1, out
    return found[0]


def test_template_has_the_defaults(tmp_path):
    (tmp_path / 'config.toml').write_text(open(os.path.join(setup.TEMPLATES, 'config.toml'), encoding='utf-8').read())
    assert '[harness]' in (tmp_path / 'config.toml').read_text()
    cfg.configure(str(tmp_path))
    assert cfg.harness_parts() == DEFAULTS


def test_no_section_gives_the_defaults(store):
    cfg.configure(store)
    assert cfg.harness_parts() == DEFAULTS


def test_optional_part_is_accepted_and_listed(store):
    set_config(store, '\n[harness]\nsubagents = "helpers/"\n')
    cfg.configure(store)
    assert cfg.harness_parts() == [(p, 'helpers/' if p == 'subagents' else d) for p, d in DEFAULTS]


def test_listed_in_parts_order(store):
    set_config(store, '\n[harness]\nhooks = "h"\nskills = "s/"\n')
    cfg.configure(store)
    assert [p for p, _ in cfg.harness_parts()] == [p for p, _ in DEFAULTS]
    assert dict(cfg.harness_parts())['skills'] == 's/' and dict(cfg.harness_parts())['hooks'] == 'h'


@pytest.mark.parametrize('body, key', [
    ('foo = "x"', 'foo'),
    ('skills = "/abs"', 'skills'),
    ('skills = "../x"', 'skills'),
    ('skills = "a/../../x"', 'skills'),
    ('skills = 3', 'skills'),
])
def test_bad_values_exit_naming_the_key(store, body, key):
    set_config(store, f'\n[harness]\n{body}\n')
    with pytest.raises(SystemExit) as exc:
        cfg.configure(store)
    assert key in str(exc.value) and 'harness' in str(exc.value)


def test_setup_reports_instructions_per_tool(home, store, capsys):
    (home / '.claude').mkdir()
    (home / '.claude/CLAUDE.md').write_text('@~/.agents/AGENTS.md\n')
    out = run(['setup', '-n'], capsys)[1]
    ins = line(out, 'instructions')
    assert 'claude-code placed' in ins and 'codex not here' in ins
    assert line(out, 'memory').count('claude-code placed') == 1


def test_setup_reports_skills_when_linked(home, store, capsys):
    (home / '.claude').mkdir()
    os.makedirs(os.path.join(store, 'skills'))
    assert 'claude-code missing' in line(run(['setup', '-n'], capsys)[1], 'skills')
    os.symlink(os.path.join(store, 'skills'), home / '.claude/skills')
    sk = line(run(['setup', '-n'], capsys)[1], 'skills')
    assert 'claude-code placed' in sk
    # the store is ~/.agents, so the tools that read it directly are placed
    assert 'codex placed' in sk and 'opencode placed' in sk and 'copilot-cli placed' in sk


def test_skills_in_another_store_are_not_read_directly(home, tmp_path, capsys):
    other = tmp_path / 'other'
    other.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(other)], check=True)
    (other / 'config.toml').write_text('[store]\nformat = 1\nredact_version = 1\n')
    (home / '.config/opencode').mkdir(parents=True)
    sk = line(run(['--store', str(other), 'setup', '-n'], capsys)[1], 'skills')
    assert 'Codex reads only ~/.agents/skills' in sk and 'opencode missing' in sk


def test_placed_parts_list_every_tool(home, store, capsys):
    got = line(run(['setup', '-n'], capsys)[1], 'subagents')
    assert got == ('subagents: agents/ (not in the store)  claude-code not here, codex not here, '
                   'copilot-cli not here, opencode not here')


def test_missing_path_is_marked(store, capsys):
    assert '(not in the store)' in line(run(['setup', '-n'], capsys)[1], 'memory')
    os.makedirs(os.path.join(store, 'memory'))
    assert '(not in the store)' not in line(run(['setup', '-n'], capsys)[1], 'memory')


def test_harness_item_is_missing_until_the_part_folders_exist(home, store, capsys):
    got = json.loads(run(['setup', '-n', '--json'], capsys)[1])
    assert 'harness' in got['missing']
    item = [i for i in got['items'] if i['name'] == 'harness'][0]
    assert item['present'] is False and len(item['details']) == 7
