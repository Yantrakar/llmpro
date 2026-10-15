"""T4: no personal word in any tracked file. Set AGENTSPRO_PERSONAL_WORDS to a comma separated list."""
import os
import re
import subprocess

import pytest

WORDS = [w.strip() for w in os.environ.get('AGENTSPRO_PERSONAL_WORDS', '').split(',') if w.strip()]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.skipif(not WORDS, reason='AGENTSPRO_PERSONAL_WORDS is not set')
def test_no_personal_words_in_tracked_files():
    names = subprocess.run(['git', '-C', ROOT, 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                           capture_output=True, text=True, check=True).stdout.split('\0')
    rx = re.compile('|'.join(re.escape(w) for w in WORDS), re.I)
    hits = []
    for name in filter(None, names):
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            continue
        if name.startswith('docs/spec/'):
            continue  # the design spec names the words to look for
        if rx.search(name):
            hits.append(name)
            continue
        try:
            text = open(path, encoding='utf-8').read()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f'{name}:{n}')
    assert not hits, 'personal words found at: ' + ', '.join(hits)
