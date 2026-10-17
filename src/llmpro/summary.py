"""Write hand-written summaries into conversation files.

  llmpro summary CONV "summary text"            CONV is a file, or tool:id of a conversation in the store
  llmpro summary --from summaries.jsonl         lines: {"id": "tool:id", "summary": "..."}

The history importer keeps these summaries on re-runs.
"""
import json
import os
import re
import sys

from .redact import load_manifest, redact


def set_summary(path, summary):
    text = open(path, encoding='utf-8').read()
    lines = '\n'.join('  ' + ln for ln in redact(summary).strip().splitlines())
    new, n = re.subn(r'^summary: \|\n(?:  .*\n|\n)*?(?=---\n|[a-z_]+:)', lambda m: 'summary: |\n' + lines + '\n',
                     text, count=1, flags=re.M)
    if n != 1:
        raise SystemExit(f'no summary field in {path}')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(new)


def conversations(store):
    return {f'{e["tool"]}:{e["original_id"]}': e for e in load_manifest(store).values() if e['kind'] == 'conversation'}


def resolve(store, conv):
    """CONV -> the file: a path that exists, else the output of the tool:id row in the manifest."""
    if os.path.isfile(conv):
        return conv
    m = conversations(store).get(conv)
    if not m or not m.get('output'):
        raise SystemExit(f'{conv}: not a file, and not a conversation in the store')
    return os.path.join(store, m['output'])


def run(store, conv=None, text=None, from_file=None):
    """-> number of summaries that could not be placed (unknown ids)."""
    if from_file:
        by_id = conversations(store)
        missing = 0
        for line in open(from_file, encoding='utf-8'):
            if not line.strip():
                continue
            e = json.loads(line)
            m = by_id.get(e['id'])
            if not m or not m.get('output'):
                print(f'unknown id {e["id"]}', file=sys.stderr)
                missing += 1
                continue
            set_summary(os.path.join(store, m['output']), e['summary'])
        return missing
    set_summary(resolve(store, conv), text)
    return 0
