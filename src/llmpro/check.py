"""Secret check for the store. Independent of the redactor: it imports nothing from redact.py and has
its own patterns, so a gap in one is not a gap in both.

Part 1, pattern scan: token prefixes, private-key headers, and random-looking strings of 16+ letters and
digits that start within 40 characters after key/secret/token/pass/auth/cred.
Part 2, leak check: collect secret values from the raw sources listed in manifest.jsonl, then confirm
that no value, and no 8-character piece of one (after its public prefix), appears anywhere in the store.

Values are kept in memory only; the cache in .git/agentspro/ holds keyed hashes of them, never the
values (see part2_cached). Output shows first 3 characters, length and a sha256 fingerprint.
A false positive goes in the store's .agentspro/allowlist.json: {"<first 16 hex of sha256>": "reason"}.
The fingerprint is of the value (part 1), of the source secret, or of the 8-character piece (part 2).

  llmpro check             # part 1 on every file in the working tree, part 2 on the whole store
  llmpro check --staged    # pre-commit: part 1 on staged files, part 2 on the whole store
  llmpro check --history   # also every blob in the store's git history
  llmpro check --no-cache  # collect every raw source again (slow), as a cross-check
Exit status 1 if anything is found that is not allowlisted.
"""
import argparse
import hashlib
import json
import math
import multiprocessing
import os
import re
import sqlite3
import subprocess

from . import REDACT_VERSION, __version__
from .store import MACHINE, data_file

# The workers read KEY, SECRETS, PIECES... as the parent set them before the pool forked.
Pool = multiprocessing.get_context('fork').Pool
ALLOWLIST_FILES = ('.agentspro/allowlist.json', 'tools/secretcheck_allowlist.json')

# ---- part 1 patterns -------------------------------------------------------------------------------
# (kind, regex, length of the public prefix that is not counted for 8-character pieces)
PREFIXED = [
    ('anthropic', re.compile(r'(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{8,}'), 7),
    ('openai', re.compile(r'(?<![A-Za-z0-9])sk-(?!ant-|ssh-|ecdsa-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{16,}'), 3),
    ('github', re.compile(r'(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{12,}'), 4),
    ('github-pat', re.compile(r'(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{12,}'), 11),
    ('gitlab', re.compile(r'(?<![A-Za-z0-9])glpat-[A-Za-z0-9_\-]{8,}'), 6),
    ('slack', re.compile(r'(?<![A-Za-z0-9])xox[a-z]-[A-Za-z0-9\-]{8,}'), 5),
    ('aws', re.compile(r'(?<![A-Za-z0-9])AKIA[0-9A-Z]{12,16}(?![A-Za-z0-9])'), 4),
    ('google', re.compile(r'(?<![A-Za-z0-9])AIza[0-9A-Za-z_\-]{12,}'), 4),
    ('huggingface', re.compile(r'(?<![A-Za-z0-9])hf_[A-Za-z0-9]{12,}'), 3),
    ('tailscale', re.compile(r'(?<![A-Za-z0-9])tskey-[a-z]+-[A-Za-z0-9\-]{8,}'), 6),
    ('npm', re.compile(r'(?<![A-Za-z0-9])npm_[A-Za-z0-9]{12,}'), 4),
    ('jwt', re.compile(r'(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}(?:\.[A-Za-z0-9_\-]+)?'), 0),
]
KEY_HEADER = re.compile(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----')
# A block is only key-shaped lines (base64, or PEM headers such as "Proc-Type: 4,ENCRYPTED") between BEGIN and
# END. A lazy BEGIN.*?END spanned whole chats from a header someone only mentioned to an END far below, and
# collected table rows and code from between them (2026-10-15).
KEY_SEP = r'(?:\s|\\+[rn])+'  # JSON-escaped newlines count too
KEY_LINE = re.compile(r'[A-Za-z0-9+/=]{16,}')
KEY_BLOCK = re.compile(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----'
                       rf'((?:{KEY_SEP}(?:[A-Za-z0-9+/=]+|[A-Za-z][A-Za-z-]*: ?[A-Za-z0-9,\-]+))*){KEY_SEP}?'
                       r'-----END [A-Z0-9 ]*PRIVATE KEY-----')
KEYWORD = re.compile(r'(?i)(?:key|secret|token|pass|auth|cred)')
# Public strings that sit next to those words: SSH public keys, and the ids APIs give to tool calls,
# messages, requests and completions (toolu_01…, chatcmpl-…).
PUBLIC = re.compile(r'(?:ssh-(?:rsa|ed25519|dss)|ecdsa-sha2-nistp\d+|sk-(?:ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com)'
                    r'[ \t]+AAAA[A-Za-z0-9+/]+=*'
                    r'|(?<![A-Za-z0-9])(?:toolu|srvtoolu|msg|req|call|resp|chatcmpl|file|batch)[_\-][A-Za-z0-9]{8,}')
RUN16 = re.compile(r'[A-Za-z0-9]{16,}')
VOWELS = set('aeiouyAEIOUY')
COMPACT_TIME = re.compile(r'\d{8}T\d{6,12}Z?')  # 20260812T143000Z in file names (2026-10-15)


def wordlike(p):
    """A run of letters that reads like a word or several words run together (StrictHost, rdsxface)."""
    low = p.lower()
    longest = max((len(r) for r in re.findall(r'[^aeiouy]+', low)), default=0)
    return longest <= 5 and sum(c in 'aeiouy' for c in low) / len(low) >= 0.15


def looks_random(s):
    """Random for its length: about as many distinct characters as a random draw of that length from its
    alphabet would give, and not made of words, identifiers or a plain number."""
    n = len(s)
    if s.isdigit() or COMPACT_TIME.fullmatch(s):
        return False
    hexchars = re.fullmatch(r'[0-9a-f]+|[0-9A-F]+', s) is not None
    k = 16 if hexchars else 26 * bool(re.search('[a-z]', s)) + 26 * bool(re.search('[A-Z]', s)) + 10 * bool(re.search('[0-9]', s))
    if len(set(s)) < 0.65 * k * (1 - (1 - 1 / k) ** n):
        return False
    pieces = re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+', s)
    if n / len(pieces) <= 3.0:
        return True  # case and digits change every few characters
    words = sum(len(p) for p in pieces if p.isalpha() and len(p) >= 4 and wordlike(p))
    return words < 0.6 * n


HASH_BEFORE = re.compile(r'(?i)(?:sha\d*|md5|digest|commit|hash|checksum|blob|tree|rev|head|@)[\s:=@\-`"\']{0,3}$')


def is_hash(text, start, v):
    """A hex digest: a hash is random but not a secret. Judged by its length and what stands before it."""
    if not re.fullmatch(r'[0-9a-f]+|[0-9A-F]+', v):
        return False
    if len(v) < 32 or len(v) == 40:
        return True  # hex ids: agent ids, key fingerprints, commit ids (as common._near_keyword, 2026-10-15)
    if len(v) not in (32, 56, 64, 96, 128):
        return False
    after = text[start + len(v):start + len(v) + 2]
    return HASH_BEFORE.search(text[max(0, start - 16):start]) is not None or after == '  '


MARKER = re.compile(r'\[REDACTED:[a-z\-]*\]?')
FINGERPRINT_BEFORE = re.compile(r'(?i)(?:sha(?:1|256|384|512)|md5):$')  # ssh-keygen fingerprints are public


def code_name(v):
    """A name in code: at most a short number, and its capitals start words of three or more letters on average
    (kBinValueMsgIdMaxLen, ddsMultiHpcsmMidPlugin, posCmd12Ready). Random letters change case every one or two."""
    if re.search(r'\d', v):
        # with a number, every word must be three letters or more (Cmd, Ready): random text has shorter pieces
        segs = re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+', v)
        return (len(re.findall(r'\d+', v)) == 1 and len(re.sub(r'\D', '', v)) <= 3 and re.search(r'[a-z][A-Z]', v)
                and all(len(g) >= 3 and (len(g) <= 4 or VOWELS & set(g)) for g in segs if g.isalpha()))
    return len(v) / (sum(c.isupper() for c in v) + 1) >= 2.5


def scan_patterns(text):
    """-> [(kind, value, offset)] for part 1."""
    hits, taken = [], []
    for kind, rx, _ in PREFIXED:
        for m in rx.finditer(text):
            hits.append((kind, m.group(0), m.start()))
            taken.append((m.start(), m.end()))
    for m in KEY_HEADER.finditer(text):
        hits.append(('private-key', m.group(0), m.start()))
    taken += [(m.start(), m.end()) for m in PUBLIC.finditer(text)]
    for m in RUN16.finditer(text):
        s = m.start()
        if any(a <= s < b for a, b in taken):
            continue
        # "[REDACTED:password]" is no keyword; mask before trimming, so a marker cut in half is masked too
        before = MARKER.sub(lambda r: ' ' * len(r.group(0)), text[max(0, s - 110):s])[-46:]
        v = m.group(0)
        if (any(k.end() >= len(before) - 40 for k in KEYWORD.finditer(before)) and looks_random(v)
                and not is_hash(text, s, v) and not code_name(v) and not FINGERPRINT_BEFORE.search(before)):
            hits.append(('near-keyword', m.group(0), s))
    return hits


# ---- part 2: collecting secret values from raw sources -------------------------------------------
NAME = r'(?:pass(?:word|wd|phrase)?|pwd|secret|token|api[_\-]?key|apikey|auth[_\-]?key|authkey|access[_\-]?key|private[_\-]?key|credentials?|requirepass|masterauth)'
NAME_RX = re.compile(r'(?i)' + NAME)
COLLECT = [
    ('quoted-assignment', re.compile(r'(?i)\b[\w.\-]*' + NAME + r'[\w\-]*["\']?\s*(?::=|=>|:|=)\s*(["\'`])([^"\'`\r\n]{6,200}?)\1'), 2),
    ('env-assignment', re.compile(r'(?m)^[ \t>]*(?:export[ \t]+)?[A-Z0-9_]*(?:PASSWORD|PASSWD|PASS|PWD|SECRET|TOKEN|API_KEY|APIKEY|AUTH_KEY|AUTHKEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIALS?)[A-Z0-9_]*[ \t]*=[ \t]*([^\s"\'`#]{6,})'), 1),
    ('yaml-assignment', re.compile(r'(?im)^[ \t>-]*[\w\-]*' + NAME + r'[\w\-]*[ \t]*:[ \t]+([^\s"\'`#{}\[\]]{6,})[ \t]*$'), 1),
    ('cli', re.compile(r'(?i)(?:--(?:password|passwd|pass|pw|token|api-key|apikey|secret|auth-key|authkey)(?:=|[ \t]+)|\bsshpass[ \t]+-p[ \t]*|\brequirepass[ \t]+|\bmasterauth[ \t]+|\bidentified[ \t]+by[ \t]+|\bwith[ \t]+(?:encrypted[ \t]+)?password[ \t]+|\bPGPASSWORD=|\bMYSQL_PWD=)(["\']?)([^\s"\'`]{6,})\1'), 2),
    ('url-credentials', re.compile(r'\b[a-z][a-z0-9+.\-]*://[^\s/:@"\'<>]{1,64}:([^\s/@"\'<>]{4,})@'), 1),
    ('auth-header', re.compile(r'(?i)\b(?:bearer|basic)[ \t]+([A-Za-z0-9._~+/\-]{12,}=*)'), 1),
    ('prose', re.compile(r'(?i)\bpassword[ \t]+(?:is[ \t]+)?(["\'`])([^"\'`\s]{4,})\1'), 2),
]
STOP = {'password', 'passwd', 'secret', 'secrets', 'token', 'tokens', 'required', 'optional', 'default', 'string',
        'none', 'null', 'true', 'false', 'undefined', 'hidden', 'masked', 'private', 'public', 'redacted',
        'changeme', 'example', 'placeholder', 'dummy', 'sample', 'testing', 'password123', 'yourpassword'}
NOT_SECRET_PARTS = ('your', 'example', 'placeholder', 'changeme', 'dummy', 'xxxx', 'redacted', '<', '>', '{{', '}}',
                    '${', 'process.env', 'environ', 'getenv', 'config.', 'settings.', 'self.', 'this.', 'args.',
                    'secrets.', 'vault_', '***', '...', '…', 'http://', 'https://')
FILE_LIKE = re.compile(r'(?i)[\w.\-]+\.(?:txt|pem|key|json|ya?ml|env|toml|cfg|conf|ini|sh|py|md|crt|pub|gpg|asc|db|sqlite)')


def classes(v):
    return (any(c.islower() for c in v) + any(c.isupper() for c in v) + any(c.isdigit() for c in v)
            + any(not c.isalnum() for c in v))


def plausible(v, kind=''):
    """Could v be a secret value, rather than a word, an identifier, a path, a host name or code?"""
    if len(v) < 6 or len(v) > 200 or v[0] in '$%<{[(*./~@#-&!:,;=\\|+' or any(c.isspace() for c in v):
        return False
    if any(c in v for c in '()[]{}\\<>`'):
        return False  # code or a regular expression
    low = v.lower()
    if low in STOP or any(p in low for p in NOT_SECRET_PARTS) or FILE_LIKE.fullmatch(v):
        return False
    if re.fullmatch(r'[A-Z][A-Z0-9_]*', v) and (not re.search(r'\d', v) or '_' in v):
        return False  # an environment variable name or metavar
    if re.fullmatch(r'[A-Za-z][a-z0-9]*(?:[_\-.:/][A-Za-z0-9]+)+', v) and not looks_random(re.sub('[^A-Za-z0-9]', '', v)):
        return False  # snake_case, kebab-case, dotted.name, host.name, a:b
    if re.fullmatch(r'[a-z]+(?:[A-Z][a-z]+)+|[A-Z][a-z]+(?:[A-Z][a-z]+)*', v):
        return False  # camelCase / PascalCase identifier
    if re.fullmatch(r'\d{4}-\d\d-\d\d(?:[T ][\d:.]+)?(?:Z|[+\-]\d\d:?\d\d)?', v):
        return False  # a date or timestamp
    if re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', low):
        return False  # a UUID: an id, not a secret
    if len(set(v)) <= 3 or re.fullmatch(r'v?\d+(?:[.\-]\d+)*', v):
        return False  # repeated characters, a number or a version
    if classes(v) >= 2 and re.search(r'[A-Za-z]', v) and (re.search(r'\d', v) or not v.isalpha()):
        return True  # mixed letters with digits or symbols, like most passwords and tokens
    alnum = re.sub('[^A-Za-z0-9]', '', v)
    return len(alnum) >= 12 and looks_random(alnum)


def collect_text(text, out, where):
    for kind, rx, grp in COLLECT:
        for m in rx.finditer(text):
            v = m.group(grp).rstrip('.,;:')
            if plausible(v, kind):
                out.append((kind, v, where))
    for kind, rx, plen in PREFIXED:
        for m in rx.finditer(text):
            out.append((kind, m.group(0), where))
    for m in KEY_BLOCK.finditer(text):
        for line in re.split(KEY_SEP, m.group(1)):
            if KEY_LINE.fullmatch(line):
                out.append(('private-key', line, where))
    for kind, v, _ in scan_patterns(text):
        if kind == 'near-keyword' and not re.fullmatch(r'[0-9a-f]{7,64}', v):  # bare hex near "key": commit ids
            out.append((kind, v, where))


def secret_field(key, v):
    """Is v a secret by the JSON key it is under? A key that ends in a secret name (password, apiKey,
    access_token) takes any plausible value; one that only contains it (staticTokens, tokenizer,
    secretName) takes only a random-looking one: "python3" under staticTokens is no secret."""
    if not plausible(v):
        return False
    compact = re.sub(r'[^a-z0-9]', '', key.lower())
    if re.search(NAME + '$', compact):
        return True
    alnum = re.sub('[^A-Za-z0-9]', '', v)
    return bool(NAME_RX.search(key)) and len(alnum) >= 12 and looks_random(alnum)


def walk_json(obj, out, where, key=''):
    if isinstance(obj, str):
        if key and secret_field(key, obj):
            out.append(('json-field', obj, where))
        collect_text(obj, out, where)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            walk_json(v, out, where, str(k))
    elif isinstance(obj, list):
        for v in obj:
            walk_json(v, out, where, key)


def read_jsonl(path, start, out):
    """Collect from a .jsonl file from byte offset start; -> the offset after the last complete line."""
    end = start
    with open(path, 'rb') as f:
        f.seek(start)
        for raw in f:
            line = raw.decode('utf-8', 'replace')
            try:
                walk_json(json.loads(line), out, path)
            except ValueError:
                collect_text(line, out, path)
            if raw.endswith(b'\n'):
                end += len(raw)  # a last line still being written is read now and again next time
    return end


CREDENTIAL_FILE = re.compile(r'(?i)(^|/)(auth\.json|\.?credentials[^/]*|settings[^/]*\.json|\.env[^/]*|id_(rsa|ed25519|ecdsa)[^/]*|[^/]*\.(pem|key|p12|pfx))$|/secrets?/')


def collect_source(path):
    """-> [(kind, value, source)] from one raw source. Runs in a worker process."""
    out = []
    try:
        if '#session/' in path:
            db, sid = path.split('#session/', 1)
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            for table in ('message', 'part'):
                for (data,) in con.execute(f'select data from {table} where session_id=?', (sid,)):
                    walk_json(json.loads(data), out, path)
            con.close()
        elif os.path.isdir(path):
            for d, dirs, files in os.walk(path, followlinks=True):
                dirs[:] = [x for x in dirs if x not in ('.git', 'node_modules', '__pycache__')]
                for f in files:
                    p = os.path.join(d, f)
                    if not CREDENTIAL_FILE.search(p):
                        out += collect_source(p)
        elif path.endswith('opencode.jsonc'):
            m = re.search(r'"instructions"\s*:\s*\[(.*?)\]', open(path, encoding='utf-8').read(), re.S)
            if m:  # only this key reaches the store; the rest is tool config
                collect_text(m.group(1), out, path)
        elif path.endswith('.jsonl'):
            read_jsonl(path, 0, out)
        elif path.endswith('.json'):
            text = open(path, encoding='utf-8', errors='replace').read()
            try:
                walk_json(json.loads(text), out, path)
            except ValueError:
                collect_text(text, out, path)
        else:
            collect_text(open(path, encoding='utf-8', errors='replace').read(), out, path)
    except (OSError, sqlite3.Error) as exc:
        out.append(('error', f'{type(exc).__name__}', path))
    return out


THIS_MACHINE = MACHINE


def raw_sources(store):
    paths, refused = [], []
    manifests = [os.path.join(store, 'manifest.jsonl'), os.path.join(store, 'local', 'manifest.jsonl')]
    for line in (ln for m in manifests if os.path.exists(m) for ln in open(m, encoding='utf-8')):
        e = json.loads(line)
        if e['machine'] != THIS_MACHINE or not e['status'].startswith('imported'):  # other machines check their own
            continue
        p = e['source_path']
        if CREDENTIAL_FILE.search(p.split('#', 1)[0]):
            refused.append(p)
        elif os.path.exists(p.split('#session/', 1)[0]):
            paths.append(p)
    return sorted(set(paths)), refused


# ---- shared helpers -------------------------------------------------------------------------------
def fp(v):
    return hashlib.sha256(v.encode('utf-8')).hexdigest()[:16]


def mask(v):
    return f'{v[:3]}…(len {len(v)}) sha256:{fp(v)}'


def public_prefix(kind, v):
    if kind == 'jwt':
        parts = v.split('.')
        return v[:len(v) - len(parts[-1])] if len(parts) == 3 and len(parts[-1]) >= 8 else v[:len(parts[0]) + 11]
    for k, rx, plen in PREFIXED:
        if k == kind:
            if kind == 'tailscale':
                return v[:v.index('-', 6) + 1]
            return v[:plen]
    return ''


def store_files(store):
    """-> {relative path: text} for every file in the working tree that git would see (tracked, or new and
    not ignored), plus the staged version of each staged file."""
    files = {}
    out = git(store, 'ls-files', '-z', '--cached', '--others', '--exclude-standard')
    for rel in sorted(set(p for p in out.decode().split('\0') if p)):
        try:
            files[rel] = open(os.path.join(store, rel), encoding='utf-8', errors='replace').read()
        except OSError:
            pass  # deleted in the working tree
    for rel in staged(store):
        files[':staged:' + rel] = git_show(store, ':' + rel)
    for rel in list(files):
        if rel.endswith(ALLOWLIST_FILES):
            files[rel] = allowlist_reasons(files[rel])
    return files


def allowlist_reasons(text):
    """The allowlist's keys are fingerprints, which look random: scan only its reasons. A key that is not
    a 16-hex fingerprint is kept, so a value pasted there by mistake is still caught."""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(data, dict):
        return text
    return '\n'.join((k + ' ' if not re.fullmatch(r'[0-9a-f]{16}', k) else '') + str(v) for k, v in data.items())


def git(store, *args):
    return subprocess.run(['git', '-C', store, *args], capture_output=True, check=True).stdout


def staged(store):
    out = git(store, 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z')
    return [p for p in out.decode().split('\0') if p]


def git_show(store, spec):
    return git(store, 'show', spec).decode('utf-8', 'replace')


def history_blob_names(store):
    """-> {sha: path} for every blob reachable from any ref."""
    objs = git(store, 'rev-list', '--all', '--objects').decode().splitlines()
    names = {}
    for line in objs:
        sha, _, path = line.partition(' ')
        names.setdefault(sha, path)
    proc = subprocess.run(['git', '-C', store, 'cat-file', '--batch-check'], input='\n'.join(names).encode(),
                          capture_output=True, check=True)
    return {l.split()[0]: names[l.split()[0]] for l in proc.stdout.decode().splitlines() if l.split()[1:2] == ['blob']}


def blob_rel(sha, path):
    return f'blob {sha[:10]} ({path})'


def read_blobs(store, names):
    """{sha: path} -> {'blob <sha> (<path>)': text}, all read by one git process."""
    shas = sorted(names)
    if not shas:
        return {}
    data = subprocess.run(['git', '-C', store, 'cat-file', '--batch'], input='\n'.join(shas).encode() + b'\n',
                          capture_output=True, check=True).stdout
    out, pos = {}, 0
    for sha in shas:
        nl = data.index(b'\n', pos)
        size = int(data[pos:nl].split()[2])
        text = data[nl + 1:nl + 1 + size].decode('utf-8', 'replace')
        pos = nl + 1 + size + 1
        path = names[sha]
        out[blob_rel(sha, path)] = allowlist_reasons(text) if path.endswith(ALLOWLIST_FILES) else text
    return out


def line_of(text, offset):
    return text.count('\n', 0, offset) + 1


# ---- the two checks -------------------------------------------------------------------------------
BAD = set()  # files with a hit in this run: a blob there is not cached as clean


def part1(files, allow, show_context, jobs=1):
    found = 0
    items = sorted(files.items(), key=lambda kv: -len(kv[1]))  # the largest first, so no worker ends last alone
    if jobs > 1 and len(items) > 1:
        with Pool(jobs) as pool:
            results = dict(zip((rel for rel, _ in items), pool.map(scan_patterns, (t for _, t in items), chunksize=1)))
    else:
        results = {rel: scan_patterns(text) for rel, text in items}
    for rel, text in sorted(files.items()):
        for kind, v, off in results[rel]:
            if fp(v) in allow:
                continue
            found += 1
            BAD.add(rel)
            print(f'PATTERN {rel}:{line_of(text, off)} [{kind}] {mask(v)}')
            if show_context:
                print(f'     after: {context(text, off)!r}')
    return found


RUN = re.compile(r'[^\s"\'`<>()\[\]{},;]+')
LONG_RUN = re.compile(r'[A-Za-z0-9_\-+/=.~]{12,}')
SECRETS, PIECES = {}, {}  # filled in the parent before the pool forks; read by match_file


def context(text, i):
    """The 40 characters before a hit, one line, with every long run masked by its length."""
    before = text[max(0, i - 40):i].replace('\n', ' ')
    return LONG_RUN.sub(lambda m: f'<{len(m.group(0))}>', before)


def match_file(item):
    rel, text = item
    hits, reported = [], set()
    for m in RUN.finditer(text):
        token = m.group(0).rstrip('.:')
        if token in SECRETS:
            reported.add(token)
            hits.append((rel, line_of(text, m.start()), 'whole', token, token, context(text, m.start())))
    for m in RUN.finditer(text):
        run = m.group(0)
        for j in range(len(run) - 7):
            v = PIECES.get(run[j:j + 8])
            if v is not None and v not in reported:
                reported.add(v)
                i = m.start() + j
                hits.append((rel, line_of(text, i), 'whole' if v in text else 'piece', v, run[j:j + 8], context(text, i)))
    return hits


def piece_ok(piece):
    """An 8-character piece worth looking for: letters with digits, or a case pattern no word has.
    Plain words, names and numbers are not looked for: they are everywhere."""
    has_letter = re.search('[A-Za-z]', piece) is not None
    has_digit = re.search('[0-9]', piece) is not None
    case_changes = len(re.findall(r'(?=[a-z][A-Z]|[A-Z][a-z])', piece))
    return (has_letter and has_digit) or case_changes >= 4


def part2(store, files, allow, jobs, show_context):
    sources, refused = raw_sources(store)
    for p in refused:
        print(f'note: not opened (credential file): {p}')
    with Pool(jobs) as pool:
        collected = [x for chunk in pool.imap_unordered(collect_source, sources, chunksize=1) for x in chunk]
    SECRETS.clear()
    PIECES.clear()
    for kind, v, src in collected:
        if kind == 'error':
            print(f'note: could not read {src}: {v}')
        elif fp(v) not in allow:
            SECRETS.setdefault(v, (kind, src))
    for v, (kind, src) in SECRETS.items():
        body = v[len(public_prefix(kind, v)):]
        for i in range(len(body) - 7):
            piece = body[i:i + 8]
            if piece_ok(piece) and fp(piece) not in allow:
                PIECES.setdefault(piece, v)
    print(f'leak check: {len(sources)} raw sources, {len(SECRETS)} distinct secret values, {len(PIECES)} 8-character pieces')
    with Pool(jobs) as pool:
        results = pool.map(match_file, sorted(files.items(), key=lambda kv: -len(kv[1])), chunksize=1)
    rows = []
    for rel, line, how, v, piece, ctx in sorted(h for r in results for h in r):
        kind, src = SECRETS[v]
        what = f'piece sha256:{fp(piece)} of ' if how == 'piece' else ''
        rows.append((rel, line, f'{how} {what}[{kind}] {mask(v)} from {src}', ctx))
    return print_leaks(rows, show_context)


def print_leaks(rows, show_context):
    """Print one line per leaked value or piece, at its first place, with how often it was found
    elsewhere: one ordinary string learned by mistake used to print thousands of lines. -> hits."""
    groups = {}
    for rel, line, what, ctx in rows:
        groups.setdefault(what, []).append((rel, line, ctx))
    for what, places in groups.items():
        rel, line, ctx = places[0]
        more = len(places) - 1
        files = len({p[0] for p in places})
        print(f'LEAK {rel}:{line} {what}' + (f'   (+{more} more, {files} files in all)' if more else ''))
        if show_context:
            print(f'     after: {ctx!r}')
    return len(rows)


# ---- part 2 with a cache --------------------------------------------------------------------------
# Collecting values from the raw sources is the slow part (minutes: some logs are over 300 MB) and almost
# none of them change between runs. The cache keeps, per source, what was collected from it as keyed
# hashes only (BLAKE2b, with a random key beside the cache): no value and no piece is written down, and
# without the key the cache cannot be brute-forced. Both live in .git/agentspro/, which is never pushed.
# A .jsonl source that only grew is read from where the last run stopped. A hit is resolved by collecting
# its source again, in memory, so it is printed and allowlisted exactly as without the cache.
CACHE_VERSION = f'{__version__}+{REDACT_VERSION}'
KEY = b''
WHOLE, HPIECES, META = {}, {}, []  # filled in the parent before the pool forks; read by match_hashed
CHECKED = []  # the keyed hashes of every value the last part2_cached run checked for
MIN_LEN = [6]


def kh(v):
    return hashlib.blake2b(v.encode('utf-8'), key=KEY, digest_size=8).digest()


def cache_dir(store):
    d = git(store, 'rev-parse', '--git-common-dir').decode().strip()
    return os.path.join(d if os.path.isabs(d) else os.path.join(store, d), 'agentspro')


def load_cache(cdir):
    """-> (key, cache dict). Creates the key on first use; a cache made with another key or another
    version of this file is dropped."""
    os.makedirs(cdir, mode=0o700, exist_ok=True)
    kpath = os.path.join(cdir, 'key')
    if not os.path.exists(kpath):
        fd = os.open(kpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, os.urandom(32))
        os.close(fd)
    key = open(kpath, 'rb').read()
    empty = {'version': CACHE_VERSION, 'key': hashlib.sha256(key).hexdigest()[:16], 'sources': {}, 'afp': {}}
    try:
        cache = json.load(open(os.path.join(cdir, 'cache.json'), encoding='utf-8'))
    except (OSError, ValueError):
        return key, empty
    if cache.get('version') != empty['version'] or cache.get('key') != empty['key']:
        return key, empty
    return key, cache


def save_cache(cdir, cache):
    tmp = os.path.join(cdir, f'cache.json.{os.getpid()}')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(cache, f, separators=(',', ':'))
    os.replace(tmp, os.path.join(cdir, 'cache.json'))


def source_sig(path):
    """What must be unchanged for a source's cached values to still hold."""
    if '#session/' in path:
        db = path.split('#session/', 1)[0]
        return [[os.path.getsize(p), os.stat(p).st_mtime_ns] for p in (db, db + '-wal') if os.path.exists(p)]
    if os.path.isdir(path):
        h = hashlib.sha256()
        for d, dirs, files in os.walk(path, followlinks=True):
            dirs[:] = sorted(x for x in dirs if x not in ('.git', 'node_modules', '__pycache__'))
            for f in sorted(files):
                p = os.path.join(d, f)
                if not CREDENTIAL_FILE.search(p):
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    h.update(f'{p}\0{st.st_size}\0{st.st_mtime_ns}\n'.encode())
        return [h.hexdigest()]
    st = os.stat(path)
    return [st.st_ino, st.st_size, st.st_mtime_ns]


def appendable(path):
    return path.endswith('.jsonl') and '#session/' not in path and not os.path.isdir(path)


def tail_hash(path, offset):
    """sha256 of the 4 KiB before offset: if it still matches, the file only grew after offset."""
    with open(path, 'rb') as f:
        f.seek(max(0, offset - 4096))
        return hashlib.sha256(f.read(offset - max(0, offset - 4096))).hexdigest()


def collect_job(job):
    """(path, start) -> (path, start, [(kind, value, source)], end offset, tail hash). Runs in a worker."""
    path, start = job
    if not appendable(path):
        return path, 0, collect_source(path), None, None
    out = []
    try:
        end = read_jsonl(path, start, out)
        return path, start, out, end, tail_hash(path, end)
    except OSError as exc:
        return path, start, out + [('error', type(exc).__name__, path)], None, None


def hashed_entry(kind, v, allow, afp):
    """-> [kind, hash of v, len(v), hashes of its 8-character pieces]. Fingerprints are kept (in afp) only
    for what the allowlist names, which is public already."""
    h = kh(v).hex()
    if fp(v) in allow:
        afp[h] = fp(v)
    pieces = []
    body = v[len(public_prefix(kind, v)):]
    for i in range(len(body) - 7):
        piece = body[i:i + 8]
        if piece_ok(piece):
            ph = kh(piece).hex()
            pieces.append(ph)
            if fp(piece) in allow:
                afp[ph] = fp(piece)
    return [kind, h, len(v), ''.join(pieces)]


def match_hashed(item):
    rel, text = item
    hits, reported = [], set()
    b2, key, whole, pieces, min_len = hashlib.blake2b, KEY, WHOLE, HPIECES, MIN_LEN[0]
    for m in RUN.finditer(text):
        token = m.group(0).rstrip('.:')
        if len(token) >= min_len:
            i = whole.get(b2(token.encode(), key=key, digest_size=8).digest())
            if i is not None:
                reported.add(i)
                hits.append((rel, line_of(text, m.start()), 'whole', i, '', context(text, m.start())))
    for m in RUN.finditer(text):
        run = m.group(0)
        for j in range(len(run) - 7):
            ph = b2(run[j:j + 8].encode(), key=key, digest_size=8).digest()
            i = pieces.get(ph)
            if i is not None and i not in reported:
                reported.add(i)
                hits.append((rel, line_of(text, m.start() + j), 'piece', i, ph.hex(), context(text, m.start() + j)))
    return hits


def part2_cached(store, files, allow, jobs, show_context, more=None):
    """more: (hashes of the values some files were checked against before, a function that returns those
    files): they are checked again only if a value not among those hashes was collected since."""
    global KEY
    sources, refused = raw_sources(store)
    for p in refused:
        print(f'note: not opened (credential file): {p}')
    cdir = cache_dir(store)
    KEY, cache = load_cache(cdir)
    old, afp = cache['sources'], cache['afp']
    todo, sigs = [], {}
    for p in sources:
        try:
            sigs[p] = source_sig(p)
        except OSError:
            sigs[p] = None
        o = old.get(p)
        if o and sigs[p] is not None and o['sig'] == sigs[p]:
            continue
        if (o and appendable(p) and o.get('offset') and sigs[p] and sigs[p][0] == o['sig'][0]
                and sigs[p][1] >= o['offset'] and tail_hash(p, o['offset']) == o.get('tail')):
            todo.append((p, o['offset']))
        else:
            todo.append((p, 0))
    fresh = {}
    if todo:
        with Pool(jobs) as pool:
            for path, start, out, end, tail in pool.imap_unordered(collect_job, todo, chunksize=1):
                errors = [x for x in out if x[0] == 'error']
                for _, exc, src in errors:
                    print(f'note: could not read {src}: {exc}')
                values = list(old[path]['values']) if start else []
                seen = {e[1] for e in values}
                for kind, v, _ in out:
                    if kind != 'error':
                        e = hashed_entry(kind, v, allow, afp)
                        if e[1] not in seen:
                            seen.add(e[1])
                            values.append(e)
                fresh[path] = {'sig': sigs[path], 'offset': end, 'tail': tail, 'values': values,
                               'keep': not errors and sigs[path] is not None}
    merged = {p: fresh.get(p) or old[p] for p in sources}
    WHOLE.clear()
    HPIECES.clear()
    META.clear()
    for p in sources:
        for kind, hv, n, ps in merged[p]['values']:
            hb = bytes.fromhex(hv)
            if afp.get(hv) in allow or hb in WHOLE:
                continue
            WHOLE[hb] = len(META)
            META.append((kind, n, p, hv))
            for i in range(0, len(ps), 16):
                if afp.get(ps[i:i + 16]) not in allow:
                    HPIECES.setdefault(bytes.fromhex(ps[i:i + 16]), WHOLE[hb])
    MIN_LEN[0] = min((m[1] for m in META), default=6)
    print(f'leak check: {len(sources)} raw sources ({len(todo)} read, the rest from the cache), '
          f'{len(META)} distinct secret values, {len(HPIECES)} 8-character pieces')
    CHECKED[:] = [m[3] for m in META]
    if more:
        new = set(CHECKED) - set(more[0])
        if new:
            extra = more[1]()
            print(f'leak check: {len(new)} new secret values since the last clean run: checking {len(extra)} cached blobs again')
            files = {**files, **extra}
    with Pool(jobs) as pool:
        results = pool.map(match_hashed, sorted(files.items(), key=lambda kv: -len(kv[1])), chunksize=1)
    hits = sorted(h for r in results for h in r)
    # resolve each hit by collecting its source again, in memory: print it as without the cache
    plain = {}
    need = sorted({META[h[3]][2] for h in hits})
    if need:
        print(f'note: reading {len(need)} raw source(s) again to show the hits')
        with Pool(jobs) as pool:
            for out in pool.map(collect_source, need, chunksize=1):
                for kind, v, _ in out:
                    if kind != 'error':
                        plain.setdefault(kh(v).hex(), v)
    rows = []
    for rel, line, how, i, ph, ctx in hits:
        kind, n, src, hv = META[i]
        v = plain.get(hv)
        if v is None:
            BAD.add(rel)
            rows.append((rel, line, f'{how} [{kind}] (len {n}) from {src}: the source has changed since; '
                                    f'run with --no-cache to see it', ctx))
            continue
        if fp(v) in allow:
            afp[hv] = fp(v)
            continue
        piece = ''
        if how == 'piece':
            body = v[len(public_prefix(kind, v)):]
            piece = next((body[k:k + 8] for k in range(len(body) - 7) if kh(body[k:k + 8]).hex() == ph), '')
            if fp(piece) in allow:
                afp[ph] = fp(piece)
                continue
            how = 'whole' if v in files[rel] else 'piece'
        what = f'piece sha256:{fp(piece)} of ' if how == 'piece' else ''
        BAD.add(rel)
        rows.append((rel, line, f'{how} {what}[{kind}] {mask(v)} from {src}', ctx))
    found = print_leaks(rows, show_context)
    cache['sources'] = {p: {k: e[k] for k in ('sig', 'offset', 'tail', 'values')}
                        for p, e in merged.items() if e.get('keep', True)}
    save_cache(cdir, cache)
    return found


# ---- history blobs with a cache ------------------------------------------------------------------
# A blob never changes, so one that passed both checks passes them again, unless the tool version, the allowlist
# or the cache key changed, or a secret value was collected since that it was not checked for. The cache
# keeps the blob ids and the keyed hashes of the values checked for, in .git/agentspro/blobs.json.
def blob_cache_key(allow):
    return {'version': CACHE_VERSION, 'key': hashlib.sha256(KEY).hexdigest()[:16],
            'allow': hashlib.sha256(json.dumps(allow, sort_keys=True).encode()).hexdigest()[:16]}


def load_blob_cache(cdir, allow):
    try:
        cache = json.load(open(os.path.join(cdir, 'blobs.json'), encoding='utf-8'))
    except (OSError, ValueError):
        return set(), []
    if cache.get('of') != blob_cache_key(allow):
        return set(), []
    return set(cache.get('clean', [])), cache.get('values', [])


def save_blob_cache(cdir, allow, clean, values):
    tmp = os.path.join(cdir, f'blobs.json.{os.getpid()}')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump({'of': blob_cache_key(allow), 'clean': sorted(clean), 'values': sorted(values)}, f, separators=(',', ':'))
    os.replace(tmp, os.path.join(cdir, 'blobs.json'))


def run(store, all=False, staged=False, history=False, jobs=None, part=None, context=False, no_cache=False,
        health=False, fix=False):
    """The check over the store. -> 0 if it passed, 1 if anything was found that is not allowlisted.
    Default (neither staged nor history): part 1 over every file in the working tree (`--all` before)."""
    global KEY
    jobs = jobs or os.cpu_count() or 4
    allow = {}
    allowlist = data_file(store, 'allowlist.json', 'secretcheck_allowlist.json')
    if os.path.exists(allowlist):
        allow = json.load(open(allowlist, encoding='utf-8'))
    # sessions/ (raw/ before 2026-10-08) holds the tools' unredacted logs; git must never see them
    raw = [p for p in git(store, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', 'sessions', 'raw')
           .decode().split('\0') if p]
    if raw:
        print(f'FAILED: git sees {len(raw)} raw log files, such as {raw[0]}; ignore sessions/ in .gitignore')
        return 1
    # local/ holds the chats of local-only projects; they must never be committed or reach the hub
    local = [p for p in git(store, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', 'local')
             .decode().split('\0') if p]
    if local:
        print(f'FAILED: git sees {len(local)} local-only files, such as {local[0]}; ignore local/ in .gitignore')
        return 1
    files = store_files(store)
    names, clean, checked, cdir = {}, set(), [], None
    if history:
        names = history_blob_names(store)
        if not no_cache:
            cdir = cache_dir(store)
            KEY = load_cache(cdir)[0]
            clean, checked = load_blob_cache(cdir, allow)
            clean &= set(names)
        files.update(read_blobs(store, {b: p for b, p in names.items() if b not in clean}))
        if clean:
            print(f'history: {len(names)} blobs, {len(clean)} of them clean in an earlier run and not scanned again')
    nh = 0
    if fix:
        from . import health as _health
        if _health.fix_index(store):
            print('fixed: memory/INDEX.md rebuilt')
    if health:
        from . import health as _health
        problems = _health.problems(store)
        for line in problems:
            print('problem: ' + line)
        nh = len(problems)
        print(f'store health: {nh} problems')
    if history or not staged:
        scan = files
    else:
        scan = {k: v for k, v in files.items() if k.startswith(':staged:')}
    n1 = n2 = 0
    if part != 2:
        n1 = part1(scan, allow, context, jobs)
        print(f'pattern scan: {len(scan)} files, {n1} hits')
    if part != 1:
        if no_cache:
            n2 = part2(store, files, allow, jobs, context)
        else:
            more = (checked, lambda: read_blobs(store, {b: names[b] for b in clean})) if clean else None
            n2 = part2_cached(store, files, allow, jobs, context, more)
        print(f'leak check: {n2} hits')
    if cdir and part is None:
        save_blob_cache(cdir, allow, {b for b, p in names.items() if blob_rel(b, p) not in BAD}, CHECKED)
    if n1 or n2:
        print('FAILED: fix the redactor or allowlist a false positive by fingerprint, with a reason')
        return 1
    if nh:
        print('FAILED: store health (llmpro check --fix rebuilds INDEX.md; the rest is for you)')
        return 1
    print('passed')
    return 0
