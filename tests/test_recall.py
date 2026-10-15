"""Seeded recall test for the redactor (redact.py) and the secret check (check.py): finds false negatives.

Run: pytest tests/test_recall.py            ( green while the gaps below stay as they are)
     python3 tests/test_recall.py --report   (matrix of case x (redact, scan, collect), recall per tool)

Every fake secret is generated at run time from a fixed seed, so no secret-shaped string sits in this file (the
store's leak check reads every file and every chat log). Nothing here ever prints a generated value: failures
and the report name cases only.

Three outcomes per case:
  redact   common.redact(text) no longer contains the value
  scan     secretcheck.scan_patterns(text) flags the value (part 1)
  collect  secretcheck.collect_text (walk_json for JSON) collects the value (part 2)
A case caught today is a plain assertion. A case missed today is named in KNOWN_GAPS and must stay missed: the
test fails when a gap starts passing, so the list stays honest. Fix the tools, then shrink the list.
"""
import base64
import json
import os
import random
import string
import sys
import unittest
import urllib.parse

from agentspro import check as secretcheck
from agentspro import redact as common

TOOLS = ('redact', 'scan', 'collect')
DASH = '-' * 5
WORDS = ['Sunflower', 'Dragonfly', 'Whiskey', 'Mountain', 'Pancake', 'Thunder', 'Kangaroo', 'Blueberry']
ALNUM = string.ascii_letters + string.digits
SYMBOLS = '!#%^*_-+=?'


class Gen:
    """Fake secrets from one seeded generator, so a run always makes the same ones."""

    def __init__(self, seed):
        self.rng = random.Random(seed)

    def chars(self, n, alphabet=ALNUM):
        return ''.join(self.rng.choice(alphabet) for _ in range(n))

    def mixed(self, n):
        """Letters and digits with every class present, so a check for random-looking text can't drop it."""
        while True:
            v = self.chars(n)
            if any(c.islower() for c in v) and any(c.isupper() for c in v) and any(c.isdigit() for c in v):
                return v

    def b64(self, nbytes, urlsafe=False):
        raw = bytes(self.rng.randrange(256) for _ in range(nbytes))
        enc = base64.urlsafe_b64encode(raw) if urlsafe else base64.b64encode(raw)
        return enc.decode().rstrip('=')

    def human(self):
        return self.rng.choice(WORDS) + str(self.rng.randrange(10, 99)) + '!'

    def with_at(self):
        return self.rng.choice(WORDS) + '@' + str(self.rng.randrange(100, 999))

    def random_pw(self):
        return self.mixed(10) + self.chars(2, SYMBOLS) + self.mixed(4)

    def jwt(self):
        head = base64.urlsafe_b64encode(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode()).decode().rstrip('=')
        claims = json.dumps({'sub': self.chars(8), 'iat': 1760000000}).encode()
        body = base64.urlsafe_b64encode(claims).decode().rstrip('=')
        return f'{head}.{body}.{self.b64(32, True)}'

    def pem_body(self):
        return [self.b64(48) for _ in range(3)] + [self.b64(30)]


def make_values(g):
    """-> {type: value} for the one-line secrets, in a fixed order."""
    return {
        'pw-human': g.human(),
        'pw-random': g.random_pw(),
        'pw-at': g.with_at(),
        'github': 'ghp_' + g.mixed(36),
        'gitlab': 'glpat-' + g.mixed(20),
        'openai': 'sk-proj-' + g.mixed(48),
        'anthropic': 'sk-ant-api03-' + g.b64(60, True),
        'aws-id': 'AKIA' + g.chars(16, string.ascii_uppercase + string.digits),
        'aws-secret': g.b64(30).replace('+', 'a').replace('/', 'b')[:40],
        'slack': 'xoxb-' + g.chars(12, string.digits) + '-' + g.chars(12, string.digits) + '-' + g.mixed(24),
        'npm': 'npm_' + g.mixed(36),
        'huggingface': 'hf_' + g.mixed(34),
        'jwt': g.jwt(),
        'apikey-mixed': g.mixed(32),
        'apikey-hex': g.chars(32, string.digits + 'abcdef'),
        'bearer': g.mixed(40),
        'dburl-pw': g.mixed(14),
    }


LABELS = {
    'pw-human': 'DB_PASSWORD', 'pw-random': 'APP_PASSWORD', 'pw-at': 'ADMIN_PASSWORD',
    'github': 'GITHUB_TOKEN', 'gitlab': 'GITLAB_TOKEN', 'openai': 'OPENAI_API_KEY', 'anthropic': 'ANTHROPIC_API_KEY',
    'aws-id': 'AWS_ACCESS_KEY_ID', 'aws-secret': 'AWS_SECRET_ACCESS_KEY', 'slack': 'SLACK_TOKEN', 'npm': 'NPM_TOKEN',
    'huggingface': 'HF_TOKEN', 'jwt': 'ACCESS_TOKEN', 'apikey-mixed': 'API_KEY', 'apikey-hex': 'SERVICE_API_KEY',
    'bearer': 'AUTH_TOKEN', 'dburl-pw': 'DB_PASSWORD',
}
BEARER_TYPES = ('github', 'gitlab', 'openai', 'anthropic', 'slack', 'npm', 'huggingface', 'jwt', 'apikey-mixed',
                'bearer')


class Case:
    """text is what the tools see; shown are the strings that must go (or be flagged, or be collected)."""

    def __init__(self, name, text, shown, collected=None, is_json=False, scan_shown=None):
        self.name, self.text, self.shown = name, text, shown
        self.collected = collected or shown  # JSON parsing can change how a value reads
        self.is_json = is_json
        self.scan_shown = scan_shown or shown


def one_line_cases(tp, v):
    """The settings that fit a one-line secret: a password, a token or a key."""
    label = LABELS[tp]
    low = label.lower()
    pw = tp.startswith('pw-')
    word = 'password' if pw else 'token'
    cases = []

    def add(setting, text, shown=None, **kw):
        cases.append(Case(f'{tp}/{setting}', text, shown or [v], **kw))

    add('env', f'{label}={v}\n')
    add('export', f'export {label}={v}\n')
    add('yaml', f'service:\n  {low}: {v}\n')
    doc = json.dumps({'service': {low: v}})
    add('json', doc, is_json=True)
    note = f'export {label}="{v}"\necho done\n'
    add('json-escaped', json.dumps({'type': 'tool_result', 'content': note}), is_json=True)
    add('prose-is', f'Note that the {word} is {v} for now.')
    add('prose-pw', f'pw: {v}')
    add('prose-use', f'use {v} to log in to the admin console.')
    add('log', f"2026-10-14 09:12:33 DEBUG auth: sent {{'{low}': '{v}', 'user': 'alice'}}")
    add('table', f'| host | field | value |\n|---|---|---|\n| prod | {word} | {v} |')
    add('code', f'{low} = "{v}"\nclient = connect({low})\n')
    add('quote', f'> {label}={v}\n> next line\n')
    cut = len(v) // 2
    add('split', f'{low}: {v[:cut]}\n{v[cut:]}\n', [v[:cut], v[cut:]])
    if pw:
        add('cli-password', f'deploy --user alice --password {v} --host db1')
        add('cli-p-flag', f'mysql -u root -p {v} -h db1')
        add('sshpass', f'sshpass -p {v} ssh alice@build1')
        enc = urllib.parse.quote(v, safe='')
        add('url-creds', f'git clone https://alice:{v}@git.example.com/team/repo.git')
        if enc != v:
            add('url-creds-encoded', f'git clone https://alice:{enc}@git.example.com/team/repo.git', [enc])
    else:
        add('cli-token', f'tool --token {v} run')
        add('url-param', f'GET https://api.example.com/v1/items?{low}={v}&limit=5')
        if tp in BEARER_TYPES:
            add('bearer-header', f'Authorization: Bearer {v}')
            add('bearer-curl', f'curl -H "Authorization: Bearer {v}" https://api.example.com/v1/me')
    return cases


def key_cases(g, tp, kind):
    """Private key blocks. The header is built at run time: a literal one would trip the leak check."""
    lines = g.pem_body()
    head = f'{DASH}BEGIN {kind}{DASH}'
    tail = f'{DASH}END {kind}{DASH}'
    block = '\n'.join([head] + lines + [tail])
    cases = []

    def add(setting, text, **kw):
        cases.append(Case(f'{tp}/{setting}', text, lines, scan_shown=[head], **kw))

    add('plain', f'Here is the deploy key:\n{block}\nthanks')
    add('json-escaped', json.dumps({'content': f'cat id_rsa\n{block}\n'}), is_json=True)
    add('quote', '\n'.join('> ' + ln for ln in block.split('\n')))
    add('yaml-block', 'ssh:\n  private_key: |\n' + '\n'.join('    ' + ln for ln in block.split('\n')) + '\n')
    add('env-escaped', 'PRIVATE_KEY="' + block.replace('\n', '\\n') + '"\n')
    add('log', '2026-10-14 09:12:33 DEBUG loaded ' + block.replace('\n', ' ') + ' ok')
    add('code', 'KEY = """\n' + block + '\n"""\nsign(KEY)\n')
    add('table', '| name | value |\n|---|---|\n| key | ' + block.replace('\n', ' ') + ' |')
    return cases


def header_cases(tp, label, secret, shown_prefix):
    """Basic-auth and bearer headers: the secret is the base64 (or token) part."""
    cases = []

    def add(setting, text, **kw):
        cases.append(Case(f'{tp}/{setting}', text, [secret], **kw))

    h = f'Authorization: {shown_prefix} {secret}'
    add('header', h)
    add('curl', f'curl -s -H "{h}" https://api.example.com/v1/me')
    add('json', json.dumps({'headers': {'Authorization': f'{shown_prefix} {secret}'}}), is_json=True)
    add('json-escaped', json.dumps({'command': f'curl -H "{h}" https://api.example.com'}), is_json=True)
    add('log', f'2026-10-14 09:12:33 DEBUG http: > GET /v1/me\n2026-10-14 09:12:33 DEBUG http: > {h}')
    add('quote', f'> {h}\n> Accept: */*\n')
    add('yaml', f'headers:\n  authorization: {shown_prefix} {secret}\n')
    add('table', f'| header | value |\n|---|---|\n| Authorization | {shown_prefix} {secret} |')
    add('env', f'AUTH_HEADER="{shown_prefix} {secret}"\n')
    return cases


def dburl_cases(tp, p):
    """Connection URLs with password p, as it reads inside the URL (URL-encoded when it has an '@')."""
    url = f'postgresql://app:{p}@db.internal:5432/app'
    cases = []

    def add(setting, text, **kw):
        cases.append(Case(f'{tp}/{setting}', text, [p], **kw))

    add('env', f'DATABASE_URL={url}\n')
    add('export', f'export DATABASE_URL="{url}"\n')
    add('yaml', f'database:\n  url: {url}\n')
    add('json', json.dumps({'database': {'url': url}}), is_json=True)
    add('json-escaped', json.dumps({'content': f'DATABASE_URL="{url}"\n'}), is_json=True)
    add('cli', f'psql "{url}" -c "select 1"')
    add('prose', f'Connect to the database with {url} from the bastion.')
    add('log', f'2026-10-14 09:12:33 INFO db: connecting to {url} (pool=5)')
    add('table', f'| env | database |\n|---|---|\n| prod | {url} |')
    add('code', f'engine = create_engine("{url}")\n')
    add('quote', f'> {url}\n')
    return cases


def build_cases(seed=20261015):
    """-> every case, in a fixed order. Same seed, same values."""
    g = Gen(seed)
    vals = make_values(g)
    cases = []
    for tp, v in vals.items():
        if tp in ('bearer', 'dburl-pw'):
            continue
        cases += one_line_cases(tp, v)
    cases += key_cases(g, 'key-rsa', 'RSA PRIVATE KEY')
    cases += key_cases(g, 'key-openssh', 'OPENSSH PRIVATE KEY')
    basic = base64.b64encode(f'alice:{g.human()}'.encode()).decode()
    cases += header_cases('basic-auth', 'Basic', basic, 'Basic')
    cases += header_cases('bearer-auth', 'Bearer', vals['bearer'], 'Bearer')
    cases += dburl_cases('dburl', vals['dburl-pw'])
    cases += dburl_cases('dburl-encoded', urllib.parse.quote(g.with_at(), safe=''))
    return cases


def covers(value, needle):
    """Does a hit or collected value stand for the needle: the whole of it, or nearly all of it?"""
    return needle in value and len(value) <= len(needle) + 6 or value in needle and len(value) >= 0.8 * len(needle)


def spans(text, needle):
    out, i = [], text.find(needle)
    while i >= 0:
        out.append((i, i + len(needle)))
        i = text.find(needle, i + 1)
    return out


def run_case(case):
    """-> {tool: bool}. LEARNED is saved and restored, as test_redaction.py does."""
    saved = dict(common.LEARNED), dict(common.LEARNED_BY)
    common.LEARNED.clear()
    try:
        out = common.redact(case.text)
    finally:
        common.LEARNED.clear()
        common.LEARNED.update(saved[0])
        common.LEARNED_BY.clear()
        common.LEARNED_BY.update(saved[1])
    redacted = not any(n in out for n in case.shown)
    hits = secretcheck.scan_patterns(case.text)
    flagged = True
    for n in case.scan_shown:
        where = spans(case.text, n)
        flagged = flagged and any(covers(v, n) and any(off < b and off + len(v) > a for a, b in where)
                                  for _, v, off in hits)
    got = []
    if case.is_json:
        secretcheck.walk_json(json.loads(case.text), got, 'case')
    else:
        secretcheck.collect_text(case.text, got, 'case')
    collected = all(any(covers(v, n) for _, v, _ in got) for n in case.collected)
    return {'redact': redacted, 'scan': flagged, 'collect': collected}


CASES = build_cases()
_RESULTS = {}


def results():
    if not _RESULTS:
        for c in CASES:
            _RESULTS[c.name] = run_case(c)
    return _RESULTS


# Missed today, as 'tool:case'. Generated from a run; keep it honest: the test fails when one starts passing.
KNOWN_GAPS = {
    # redact: split across lines: no rule joins a value cut by a newline, and each half is too short or plain
    'redact:pw-human/split',
    'redact:pw-random/split',
    'redact:pw-at/split',
    'redact:gitlab/split',
    'redact:openai/split',
    'redact:aws-id/split',
    'redact:jwt/split',
    # a hex key cut in two: each half is under 32 hex characters, which are ids (agent ids, fingerprints) (2026-10-15)
    'redact:apikey-hex/split',
    'scan:apikey-hex/split',
    # scan: part 1 has no password rule: only prefixed tokens, key headers and random text near a keyword
    'scan:pw-human/env',
    'scan:pw-human/export',
    'scan:pw-human/yaml',
    'scan:pw-human/json',
    'scan:pw-human/json-escaped',
    'scan:pw-human/prose-is',
    'scan:pw-human/prose-pw',
    'scan:pw-human/prose-use',
    'scan:pw-human/log',
    'scan:pw-human/table',
    'scan:pw-human/code',
    'scan:pw-human/quote',
    'scan:pw-human/split',
    'scan:pw-human/cli-password',
    'scan:pw-human/cli-p-flag',
    'scan:pw-human/sshpass',
    'scan:pw-human/url-creds',
    'scan:pw-human/url-creds-encoded',
    'scan:pw-random/env',
    'scan:pw-random/export',
    'scan:pw-random/yaml',
    'scan:pw-random/json',
    'scan:pw-random/json-escaped',
    'scan:pw-random/prose-is',
    'scan:pw-random/prose-pw',
    'scan:pw-random/prose-use',
    'scan:pw-random/log',
    'scan:pw-random/table',
    'scan:pw-random/code',
    'scan:pw-random/quote',
    'scan:pw-random/split',
    'scan:pw-random/cli-password',
    'scan:pw-random/cli-p-flag',
    'scan:pw-random/sshpass',
    'scan:pw-random/url-creds',
    'scan:pw-at/env',
    'scan:pw-at/export',
    'scan:pw-at/yaml',
    'scan:pw-at/json',
    'scan:pw-at/json-escaped',
    'scan:pw-at/prose-is',
    'scan:pw-at/prose-pw',
    'scan:pw-at/prose-use',
    'scan:pw-at/log',
    'scan:pw-at/table',
    'scan:pw-at/code',
    'scan:pw-at/quote',
    'scan:pw-at/split',
    'scan:pw-at/cli-password',
    'scan:pw-at/cli-p-flag',
    'scan:pw-at/sshpass',
    'scan:pw-at/url-creds',
    'scan:pw-at/url-creds-encoded',
    'scan:dburl/env',
    'scan:dburl/export',
    'scan:dburl/yaml',
    'scan:dburl/json',
    'scan:dburl/json-escaped',
    'scan:dburl/cli',
    'scan:dburl/prose',
    'scan:dburl/log',
    'scan:dburl/table',
    'scan:dburl/code',
    'scan:dburl/quote',
    'scan:dburl-encoded/env',
    'scan:dburl-encoded/export',
    'scan:dburl-encoded/yaml',
    'scan:dburl-encoded/json',
    'scan:dburl-encoded/json-escaped',
    'scan:dburl-encoded/cli',
    'scan:dburl-encoded/prose',
    'scan:dburl-encoded/log',
    'scan:dburl-encoded/table',
    'scan:dburl-encoded/code',
    'scan:dburl-encoded/quote',
    # scan: split token: the 2nd half has no prefix and no keyword within 40 characters
    'scan:gitlab/split',
    'scan:anthropic/split',
    'scan:aws-id/split',
    'scan:jwt/split',
    # scan: prose-pw / prose-use: the keyword is too far, or "use X" names none
    'scan:aws-secret/prose-pw',
    'scan:aws-secret/prose-use',
    'scan:apikey-mixed/prose-pw',
    'scan:apikey-mixed/prose-use',
    'scan:apikey-hex/prose-pw',
    'scan:apikey-hex/prose-use',
    # collect: prose without quotes: the "prose" collector wants a quoted value; "pw:"/"use X" have none
    'collect:pw-human/prose-is',
    'collect:pw-human/prose-pw',
    'collect:pw-human/prose-use',
    'collect:pw-random/prose-is',
    'collect:pw-random/prose-pw',
    'collect:pw-random/prose-use',
    'collect:pw-at/prose-is',
    'collect:pw-at/prose-pw',
    'collect:pw-at/prose-use',
    'collect:aws-secret/prose-pw',
    'collect:aws-secret/prose-use',
    'collect:apikey-mixed/prose-pw',
    'collect:apikey-mixed/prose-use',
    'collect:apikey-hex/prose-is',
    'collect:apikey-hex/prose-pw',
    'collect:apikey-hex/prose-use',
    # collect: table cell and split lines: no collector sees a name and value across cells or lines
    'collect:pw-human/table',
    'collect:pw-human/split',
    'collect:pw-random/table',
    'collect:pw-random/split',
    'collect:pw-at/table',
    'collect:pw-at/split',
    'collect:gitlab/split',
    'collect:anthropic/split',
    'collect:aws-id/split',
    'collect:jwt/split',
    'collect:apikey-hex/table',
    'collect:apikey-hex/split',
    # collect: bare "-p X" flag: "cli" collector knows --password etc. and sshpass -p only
    'collect:pw-human/cli-p-flag',
    'collect:pw-random/cli-p-flag',
    'collect:pw-at/cli-p-flag',
    # collect: chat quote of a key block: "> " breaks KEY_BLOCK
    'collect:key-rsa/quote',
    'collect:key-openssh/quote',
    # collect: raw "@" in a URL password / hex key in a URL parameter
    'collect:pw-at/url-creds',
    'collect:apikey-hex/url-param',
}


class Recall(unittest.TestCase):
    def test_case_names_are_unique(self):
        names = [c.name for c in CASES]
        self.assertEqual(len(names), len(set(names)))

    def test_values_appear_in_their_text(self):
        for c in CASES:
            for n in c.shown + c.scan_shown:
                self.assertIn(n, c.text, c.name)

    def test_known_gaps_name_real_cases(self):
        valid = {f'{t}:{c.name}' for c in CASES for t in TOOLS}
        self.assertEqual(sorted(KNOWN_GAPS - valid), [])

    def test_recall(self):
        res = results()
        for c in CASES:
            for tool in TOOLS:
                key = f'{tool}:{c.name}'
                with self.subTest(key):
                    if key in KNOWN_GAPS:
                        self.assertFalse(res[c.name][tool], f'{key} is caught now: remove it from KNOWN_GAPS')
                    else:
                        self.assertTrue(res[c.name][tool], f'{key} is missed')


def report():
    """Matrix of case x tool, then recall per tool. Case names only: never a generated value."""
    res = results()
    width = max(len(c.name) for c in CASES)
    print(f'{"case".ljust(width)}  redact scan collect')
    for c in CASES:
        r = res[c.name]
        print(f'{c.name.ljust(width)}  ' + '      '.join('✓' if r[t] else '✗' for t in TOOLS))
    print()
    for t in TOOLS:
        hit = sum(res[c.name][t] for c in CASES)
        print(f'{t.ljust(8)} {hit}/{len(CASES)} caught, recall {hit / len(CASES):.1%}')
    print(f'{len(CASES)} cases, {len(KNOWN_GAPS)} known gaps')


if __name__ == '__main__':
    if '--report' in sys.argv:
        report()
    else:
        unittest.main()
