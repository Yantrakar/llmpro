"""`hook` and `merge-driver`: what git runs. They import only what they need (not the importers, not
argparse), because git starts them on every commit, merge and push."""
import json
import subprocess
import sys

from .store import MACHINE

HOOKS = ('pre-commit', 'pre-merge-commit', 'pre-push')


def toplevel():
    """The work tree git is running the hook in (hooks run at its top)."""
    return subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True,
                          check=True).stdout.strip()


def hook(name):
    """Both secret checks must pass before a commit, and over every file and blob in history before a push.
    A merge commit does not run pre-commit, so pre-merge-commit runs the same check on the merged result."""
    from . import check
    return check.run(toplevel(), history=(name == 'pre-push'), staged=(name != 'pre-push'))


def rows(path):
    out = []
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def merge_manifest(ours, theirs):
    """Git merge driver for manifest.jsonl: each machine owns its own rows.

    Rows whose machine is this one come from our side, rows of every other machine from theirs (a machine
    only ever rewrites its own rows, so theirs is the newer copy of the rest). Writes the result to ours."""
    merged = {}
    for e in rows(theirs):
        if e['machine'] != MACHINE:
            merged[(e['tool'], e['machine'], e['kind'], e['original_id'])] = e
    for e in rows(ours):
        key = (e['tool'], e['machine'], e['kind'], e['original_id'])
        if e['machine'] == MACHINE or key not in merged:
            merged[key] = e
    out = sorted(merged.values(), key=lambda e: (e['machine'], e['tool'], e['kind'], e['source_path'], e['original_id']))
    with open(ours, 'w', encoding='utf-8') as fh:
        for e in out:
            fh.write(json.dumps(e, ensure_ascii=False) + '\n')
    return 0


def main(argv):
    """argv: ['hook', NAME] or ['merge-driver', 'manifest', BASE, OURS, THEIRS]. -> exit status."""
    try:
        if argv[0] == 'hook' and len(argv) >= 2 and argv[1] in HOOKS:
            return hook(argv[1])
        if argv[0] == 'merge-driver' and len(argv) >= 5 and argv[1] == 'manifest':
            return merge_manifest(argv[3], argv[4])
    except KeyboardInterrupt:
        return 130
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f'llmpro: {exc}', file=sys.stderr)
        return 2
    print('usage: llmpro hook pre-commit|pre-merge-commit|pre-push\n'
          '       llmpro merge-driver manifest BASE OURS THEIRS', file=sys.stderr)
    return 2
