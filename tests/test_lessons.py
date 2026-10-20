"""Lessons: cue, evidence and confidence in memories (spec 09)."""
import json
import os
import time

import pytest

from llmpro import cli
from llmpro.importer import memory as import_memory
from llmpro.redact import read_frontmatter

HIST = 'history/m1/codex/2026/2026-09-10_alpha_aaaa1111.md'


def put(store, rel, text):
    p = os.path.join(store, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(text)


def mem(name, extra=''):
    return (f'---\nname: {name}\ndescription: a fact\ntype: feedback\nscope: global\nstatus: active\n{extra}'
            f'created: 2026-09-01\nupdated: 2026-09-30\n---\nThe body.\n')


@pytest.fixture
def filled(store):
    put(store, HIST, '---\nid: codex:abc\ntool: codex\nmachine: m1\ntitle: A\n---\n### user\nhi\n')
    row = {'tool': 'codex', 'machine': 'm1', 'kind': 'session', 'source_path': '/x', 'original_id': 'abc',
           'mtime': '', 'size': 1, 'output': HIST, 'status': 'imported'}
    put(store, 'manifest.jsonl', json.dumps(row) + '\n')
    return store


def check(store, capsys, *memories):
    for name, extra in memories:
        put(store, f'memory/{name}.md', mem(name, extra))
    cli.main(['check', '--fix', '--store', store])
    capsys.readouterr()
    code = cli.main(['check', '--store', store])
    return code, capsys.readouterr().out


def test_valid_lesson_passes(filled, capsys):
    extra = f'cue: when the build fails\nevidence: [{HIST}, codex:abc]\nconfidence: medium\n'
    code, out = check(filled, capsys, ('a', extra))
    assert code == 0, out


def test_single_string_evidence_is_a_list_of_one(filled, capsys):
    code, out = check(filled, capsys, ('a', f'evidence: {HIST}\n'))
    assert code == 0, out


def test_no_lesson_fields_is_fine(filled, capsys):
    code, out = check(filled, capsys, ('a', ''))
    assert code == 0, out


@pytest.mark.parametrize('extra', [
    'evidence: [history/m1/codex/2026/nope.md]\n',
    'evidence: [local/history/m1/codex/2026/x.md]\n',
    'evidence: [sessions/x]\n',
    'evidence: [../x]\n',
    'evidence: [/abs/x]\n',
    'evidence: [Not A Source]\n',
    f'evidence: [{HIST}]\nconfidence: sure\n',
])
def test_bad_evidence_is_one_problem(filled, capsys, extra):
    code, out = check(filled, capsys, ('a', extra))
    assert code == 1
    lines = [ln for ln in out.splitlines() if ln.startswith('problem: ')]
    assert len(lines) == 1 and 'cite evidence that is wrong' in lines[0] and 'memory/a.md' in lines[0]


def test_two_bad_memories_one_line(filled, capsys):
    code, out = check(filled, capsys, ('a', 'evidence: [sessions/x]\n'), ('b', 'confidence: sure\n'))
    lines = [ln for ln in out.splitlines() if ln.startswith('problem: ')]
    assert code == 1 and len(lines) == 1 and lines[0].startswith('problem: 2 memories')


# ---------------------------------------------------------------- importer

SRC = ('---\nname: x\ndescription: when it fails\ntype: feedback\n'
       'cue: the build fails twice\nevidence: [history/m1/codex/2026/a.md, local/history/b.md, codex:abc]\n'
       'confidence: high\n---\nBody.\n')


def claude_memory(home, text):
    p = home / '.claude' / 'projects' / '-proj' / 'memory' / 'x.md'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_importer_keeps_lesson_fields_in_order(store, home):
    claude_memory(home, SRC)
    import_memory.run(store)
    path = os.path.join(store, 'memory', 'x.md')
    text = open(path).read()
    f, _ = read_frontmatter(path)
    assert f['cue'] == 'the build fails twice'
    assert f['evidence'] == ['history/m1/codex/2026/a.md', 'codex:abc']
    assert f['confidence'] == 'high'
    keys = [ln.split(':')[0] for ln in text.split('---')[1].strip().splitlines()]
    assert keys.index('sources') < keys.index('cue') < keys.index('evidence') < keys.index('confidence')
    assert 'local/' not in text


def test_store_edit_of_cue_survives_unchanged_source(store, home):
    src = claude_memory(home, SRC)
    import_memory.run(store)
    path = os.path.join(store, 'memory', 'x.md')
    text = open(path).read().replace('the build fails twice', 'edited in the store')
    open(path, 'w').write(text)
    import_memory.run(store)
    assert read_frontmatter(path)[0]['cue'] == 'edited in the store'
    # the source changed: the store's cue still wins over the source's
    src.write_text(SRC + 'More.\n')
    os.utime(src, (time.time() + 5, time.time() + 5))
    import_memory.run(store)
    assert read_frontmatter(path)[0]['cue'] == 'edited in the store'


def test_format_template_has_lesson_fields():
    from importlib import resources
    text = (resources.files('llmpro') / 'templates' / 'FORMAT.md').read_text(encoding='utf-8')
    for want in ('cue:', 'evidence:', 'confidence:', "Google Research's harness2 (Apache 2.0)"):
        assert want in text
