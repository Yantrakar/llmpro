"""Near-miss report: strings in the store that look like they could be secrets, but that neither the redactor
(redact.py) nor the checker (check.py) redacted or flagged. They are candidate FALSE NEGATIVES, listed
so that a person can review them and, where one is real, teach the redactor about it.

A review aid, not a gate: it always exits 0 and is not part of the pre-commit check. Many lines will be
public (ids, hashes, test strings); the point is to read the top of the list, not to reach zero.

Reasons, strongest first:
  decoded    a URL-encoded or base64 segment that decodes to something credential-shaped (user:pass, a token prefix)
  random     a run of 12+ characters that looks random (check.looks_random) and is no public shape
  split      a random run cut by a line break or a \\n escape, whose joined form is random
  near-word  a 6+ character value within 40 characters after password, token, key, pin and the like

Candidates are grouped by value (sha256 fingerprint), ranked by reason, then number of files, then hits.
A line shows the reason, the masked value (check.mask: 3 characters, length, fingerprint), hits, files
and the first place. Values are never printed in full; --context adds 30 characters either side with the
value as <VALUE> and any other long run replaced by its length.

  agentspro check --near-miss                 # the whole store: history, memory, instructions, skills
  agentspro check --near-miss --top 50        # only the 50 best groups (default 100, 0 = all)
  agentspro check --near-miss --context
"""
import base64
import binascii
import multiprocessing
import os
import re
import urllib.parse
from bisect import bisect_right
from functools import lru_cache

from .check import (KEYWORD, MARKER, PUBLIC, PREFIXED, code_name, fp, is_hash, looks_random, mask,  # noqa: F401
                    classes, plausible, scan_patterns, wordlike)

DIRS = ('history', 'memory', 'instructions', 'skills')
SKIP_DIRS = {'.git', 'synced', 'node_modules', '__pycache__'}  # synced: Claude's copy of the claude.ai skills
BINARY_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.zip', '.gz', '.xz', '.bz2', '.tar',
              '.woff', '.woff2', '.ttf', '.otf', '.sqlite', '.db', '.pyc', '.so', '.bin'}
WEIGHT = {'decoded': 4, 'random': 3, 'split': 3, 'near-word': 1}

RUN12 = re.compile(r'[A-Za-z0-9_+/=-]{12,}')
# ids that APIs hand out (as common._PUBLIC_ID, plus the other families seen in chat logs)
PUBLIC_ID = re.compile(r'(?:toolu|srvtoolu|msg|req|call|resp|chatcmpl|cmpl|file|batch|rs|fc|ws|item|run|thread|asst|'
                       r'evt|gen|vs|step|msgbatch|session|sess|ses)[_-][A-Za-z0-9]{8,}')
TURN_ID = re.compile(r'turn\d+[a-z]+\d+')  # citation ids in ChatGPT exports
UUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
HEX = re.compile(r'(?:[0-9a-f]{7,64}|[0-9A-F]{7,64})')
FINGERPRINT = re.compile(r'(?i)(?:sha(?:1|256|384|512)|md5)[:\-=]\s*$')
DATA_URI = re.compile(r'(?i)(?:data:[\w/+.\-]*;base64,|base64,|;base64)\s*$')
IMAGE_HEAD = ('iVBORw0KGgo', '/9j/', 'R0lGOD', 'UklGR', 'AAAA')  # PNG, JPEG, GIF, WEBP, and ssh/other binary blobs
NEAR_WORD = re.compile(r'(?i)(?<![A-Za-z0-9_./-])(?:password|passwd|passphrase|pw|pass|secret|token|key|apikey|'
                       r'api[_-]key|credential|login|pin)s?(?![A-Za-z0-9_-])')
TOKEN = re.compile(r'[^\s"\'`<>()\[\]{},;|*]+')
FILLER = {'is', 'are', 'was', 'the', 'a', 'an', 'for', 'to', 'of', 'set', 'as', 'with', 'and', 'or', 'in', 'on', 'at',
          'by', 'from', 'that', 'this', 'it', 'not', 'no', 'if', 'then', 'be', 'will', 'can', 'use', 'your', 'my'}
PERCENT = re.compile(r'[^\s"\'`<>()\[\]{},;|*]*%[0-9A-Fa-f]{2}[^\s"\'`<>()\[\]{},;|*]*')
B64 = re.compile(r'(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/=_-])')
SPLIT_BREAK = re.compile(r'([A-Za-z0-9_+/=-]{6,})(\n|\\n)([A-Za-z0-9_+/=-]{6,})')
URL_CREDS = re.compile(r'://[^/\s:@]+:[^/\s@]+@')
PAIR = re.compile(r'[\w.+\-@]{2,}:[^\s:/@]{4,}')
ASSIGN = re.compile(r'(?i)(?:pass\w*|pwd|secret|token|api[_-]?key|credentials?|auth\w*)\s*[=:]\s*(\S{6,})')


def is_name(run):
    """A kebab-case or snake_case name made of words and short numbers (claude-opus-4-5, my_long_name)."""
    parts = [p for p in re.split(r'[-_]', run) if p]
    if len(parts) < 2:
        return False
    def word(p):
        m = re.fullmatch(r'([A-Za-z]+)\d{0,2}[A-Za-z]?', p)
        return bool(m) and wordlike(m.group(1))
    return all((p.isdigit() and len(p) <= 8) or len(p) <= 3 or word(p) for p in parts)


def mostly_words(run):
    """Half or more of the letters sit in word-like pieces of 4+ letters (AppleWebKit537, vscode-server-x64-1)."""
    alnum = re.sub('[^A-Za-z0-9]', '', run)
    pieces = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])', alnum)
    return sum(len(p) for p in pieces if len(p) >= 4 and wordlike(p)) >= 0.5 * len(alnum)


@lru_cache(maxsize=200000)
def random_run(run):
    """-> the value to judge if run is random-looking and has no public shape that needs no context, else ''.
    Cached: the same few thousand strings recur across the store."""
    if run.endswith('=') or run.endswith('=='):
        run = run.rstrip('=')  # base64 padding
    if '=' in run:
        run = run.rsplit('=', 1)[1]  # key=value: judge the value
    run = run.lstrip('-_+/')
    alnum = re.sub('[^A-Za-z0-9]', '', run)
    if len(run) < 12 or len(alnum) < 12 or len(run) > 255:
        return ''
    if (UUID.fullmatch(run) or HEX.fullmatch(alnum) and len(alnum) == len(run) or PUBLIC_ID.match(run)
            or TURN_ID.match(run)):
        return ''
    if run.startswith(IMAGE_HEAD) and len(run) > 40 or is_name(run) or code_name(alnum):
        return ''
    if re.fullmatch(r'v?\d[\d.\-_]*[A-Za-z]?', run) or re.fullmatch(r'\d{4}-?\d\d-?\d\d[T_\-]?[\d:\-]*Z?', run):
        return ''  # a version, a date, a timestamp
    if '/' in run:
        segs = run.split('/')
        if any(len(s) >= 3 and s.isalpha() and s.islower() and wordlike(s) for s in segs) or run.count('/') > 3:
            return ''  # a path
    if re.fullmatch(r'[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+', run) or HEX.fullmatch(re.split(r'[-_]', run, 1)[-1]):
        return ''  # ENV_NAME, or a hex id behind a prefix (br-3f9a1c2d4e5b)
    if mostly_words(run) or not looks_random(alnum):
        return ''
    return run


def line_table(text):
    """Offsets of the newlines, to turn an offset into a line number."""
    return [m.start() for m in re.finditer('\n', text)]


def public_context(text, s, e, run):
    """Does what stands around text[s:e] make it public: part of a path or URL, a fingerprint, a hex digest, a
    data URI or an ssh key?"""
    before = text[max(0, s - 16):s]
    if before[-1:] in ('/', '~', '.', '@'):
        return True  # the tail of a path, a dotted name or an address
    if text[e:e + 1] == '(' or re.search(r'(?i)(?:function|def|class|set\(|"(?:node_)?id": ?")\s*$', before):
        return True  # a function name, or the id field of an API object
    after = text[e:e + 6]
    if re.match(r'\.\d', after) or re.match(r'\.[a-z]{1,5}\b', after) or re.match(r'@[\w-]', after):
        return True  # a file name or dotted name (<run>.yml), or the local part of an address
    if FINGERPRINT.search(before) or DATA_URI.search(text[max(0, s - 24):s]) or is_hash(text, s, run):
        return True
    ws = max(text.rfind(c, max(0, s - 300), s) for c in ' \t\n"\'`(<[')  # start of the token that holds the run
    head = text[ws + 1:s]
    if head.endswith('='):
        return False  # key=value in a URL query: the value is judged
    if '://' in head and not URL_CREDS.search(head + run):
        return True
    return head.startswith(('/', '~', './', '../')) or bool(re.search(r'\.[a-z]{1,5}/', head))


def mark_spans(text):
    return [(m.start(), m.end()) for m in MARKER.finditer(text)]


def touches_marker(marks, s, e):
    """Inside a [REDACTED:…] marker, or within one character of one."""
    return any(a - 1 <= e and s <= b + 1 for a, b in marks)


def credential_shaped(decoded):
    """Does decoded text read like a credential: user:pass, a token prefix, or name=secret?"""
    if '[REDACTED' in decoded or len(decoded) < 8 or len(decoded) > 300:
        return False
    if sum(32 <= ord(c) < 127 for c in decoded) < 0.95 * len(decoded):
        return False
    if any(kind != 'near-keyword' for kind, _, _ in scan_patterns(decoded)) or URL_CREDS.search(decoded):
        return True
    m = PAIR.fullmatch(decoded.strip())
    left = decoded.split(':', 1)[0]
    if m and not left.isdigit() and not re.match(r'(?i)(?:https?|ftp|file|ssh|data|mailto|urn)$', left):
        return plausible(decoded.strip().split(':', 1)[1])
    m = ASSIGN.search(decoded)
    return bool(m and plausible(m.group(1).rstrip('.,;')))


def decoded_hits(text, taken):
    """-> [(value, start, end)] for percent-encoded or base64 segments that decode to a credential."""
    out = []
    for m in PERCENT.finditer(text):
        tok = m.group(0)
        if len(tok) < 8 or any(a <= m.start() < b for a, b in taken):
            continue
        dec = urllib.parse.unquote(tok)
        if dec != tok and credential_shaped(dec.split('?')[-1] if '://' not in dec else dec):
            out.append((tok, m.start(), m.end()))
    for m in B64.finditer(text):
        tok = m.group(0)
        if len(tok) % 4 and not tok.endswith('=') or not re.search(r'[A-Z]', tok) or not re.search(r'[a-z0-9]', tok):
            continue
        if tok.startswith(IMAGE_HEAD) or PUBLIC_ID.match(tok) or any(a <= m.start() < b for a, b in taken):
            continue
        if DATA_URI.search(text[max(0, m.start() - 24):m.start()]):
            continue
        try:
            dec = base64.b64decode(tok + '=' * (-len(tok) % 4), validate=True).decode('ascii')
        except (binascii.Error, ValueError):
            continue
        if ':' in dec or '=' in dec:  # cheap filter before the full shape check
            if credential_shaped(dec):
                out.append((tok, m.start(), m.end()))
    return out


def near_word_hits(text, taken, marks):
    """-> [(value, start, end)]: the first real word after password/token/key/... if it could be a secret."""
    out = []
    for k in NEAR_WORD.finditer(text):
        window = text[k.end():k.end() + 40]
        for m in TOKEN.finditer(window):
            raw = m.group(0).strip('.,:')
            if '=' in raw:
                raw = raw.split('=', 1)[1]  # --token=abc, key=abc
            v = raw.strip('.,:')
            if v.lower() in FILLER:
                continue  # "password is X": look at X
            if (not v or not plausible(v) or UUID.search(v) or classes(v) < 3 or NOT_SECRET.match(v)):
                break  # only the first real word after the keyword counts; a human password mixes 3+ classes
            s = k.end() + m.start() + m.group(0).find(v)
            e = s + len(v)
            if (any(a <= s < b for a, b in taken) or touches_marker(marks, s, e) or PUBLIC_ID.match(v)
                    or HEX.fullmatch(v) or code_name(v) or is_name(v) or public_context(text, s, e, v)):
                break
            if FILE_OR_FLAG.match(v) or re.search(r'[a-z]{2,}\.[a-z]{2,}', v):
                break
            out.append((v, s, e))
            break
    return out


NOT_SECRET = re.compile(r'(?i)\d+x\d+|\d+:|v?\d+\.\d|sha\d+:|id_(?:rsa|ed25519|ecdsa)')  # sizes, line numbers, versions
FILE_OR_FLAG = re.compile(r'--|\w+://|[\w.\-]+\.(?:md|py|json|txt|sh|yml|yaml|toml|cfg|conf|log)$')


def split_hits(text, taken, marks):
    """-> [(joined value, start, end)] for a random run broken by a newline or a \\n escape."""
    out = []
    for m in SPLIT_BREAK.finditer(text):
        a, b = m.group(1), m.group(3)
        s, e = m.start(), m.end()
        if (text[s - 1:s] in ('/', '.', '~') or any(x <= s < y for x, y in taken) or touches_marker(marks, s, e)):
            continue
        joined = (a + b).strip('-_=+/')
        alnum = re.sub('[^A-Za-z0-9]', '', joined)
        if len(alnum) < 16 or not re.search(r'\d', alnum) or not re.search(r'[A-Za-z]', alnum) or HEX.fullmatch(alnum):
            continue
        if len(a) < 16 or HEX.fullmatch(a) and HEX.fullmatch(b) or random_run(a) or random_run(b):
            continue  # too short to be a wrapped token, hex lines, or a part that is random alone (reported as random)
        if is_name(joined) or UUID.fullmatch(joined):
            continue  # a part that is random alone is reported as random
        if not looks_random(alnum) or any(wordlike(p) and len(p) > 5 for p in re.findall(r'[A-Za-z]+', a + ' ' + b)):
            continue
        out.append((joined, s, e))
    return out


def ctx_of(text, s, e):
    """30 characters either side, the value as <VALUE>, other long runs as their length (they may be secrets too)."""
    def squash(t):
        return re.sub(r'[A-Za-z0-9_+/=.~-]{12,}', lambda m: f'<{len(m.group(0))}>', t.replace('\n', ' '))
    return squash(text[max(0, s - 30):s]) + '<VALUE>' + squash(text[e:e + 30])


def analyse(item):
    """(rel, abs path) -> (rel, {fp: [reason, masked, hits, first line, first ctx]}). Runs in a worker."""
    rel, path = item
    try:
        data = open(path, 'rb').read()
    except OSError:
        return rel, {}
    if b'\0' in data[:8192]:
        return rel, {}
    text = data.decode('utf-8', 'replace')
    taken = [(m.start(), m.end()) for m in PUBLIC.finditer(text)]
    hits = scan_patterns(text)
    for _, v, off in hits:
        taken.append((off, off + len(v)))
    marks = mark_spans(text)
    found = {}  # (start, end) -> (reason, value)
    for m in RUN12.finditer(text):
        s, e = m.start(), m.end()
        run = random_run(m.group(0))
        if not run:
            continue
        s = m.start() + m.group(0).rfind(run)  # the run may be only the value of key=value
        e = s + len(run)
        if any(a <= s < b for a, b in taken) or touches_marker(marks, s, e) or public_context(text, s, e, run):
            continue
        found[(s, e)] = ('random', run)
    for v, s, e in decoded_hits(text, taken):
        found[(s, e)] = ('decoded', v)
    for v, s, e in split_hits(text, taken, marks):
        found[(s, e)] = ('split', v)
    covered = [k for k in found]
    for v, s, e in near_word_hits(text, taken, marks):
        if not any(a <= s and e <= b for a, b in covered):
            found[(s, e)] = ('near-word', v)
    if not found:
        return rel, {}
    nl = line_table(text)
    groups = {}
    for (s, e), (reason, v) in sorted(found.items()):
        g = groups.get(fp(v))
        if g is None:
            groups[fp(v)] = [reason, mask(v), 1, bisect_right(nl, s) + 1, ctx_of(text, s, e)]
        else:
            g[2] += 1
    return rel, groups


def collect_files(store, paths):
    """-> [(relative path, absolute path)] of the text files to read, biggest first."""
    roots = [os.path.join(store, d) for d in DIRS] if not paths else [
        p if os.path.isabs(p) else os.path.join(store, p) for p in paths]
    files = []
    for root in roots:
        if os.path.isfile(root):
            files.append(root)
            continue
        for d, dirs, names in os.walk(root):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            files += [os.path.join(d, n) for n in sorted(names) if os.path.splitext(n)[1].lower() not in BINARY_EXT]
    files = [p for p in files if os.path.isfile(p) and not os.path.islink(p)]
    files.sort(key=lambda p: -os.path.getsize(p))
    return [(os.path.relpath(p, store), p) for p in files]


def run(store, top=100, paths=None, context=False, jobs=None):
    """The report, printed to stdout. -> 0 always."""
    files = collect_files(store, paths)
    merged = {}  # fp -> [reason, masked, hits, files, first rel, first line, first ctx]
    with multiprocessing.Pool(jobs or os.cpu_count() or 4) as pool:
        for rel, groups in pool.imap_unordered(analyse, files, chunksize=1):
            for key, (reason, masked, n, line, ctx) in groups.items():
                g = merged.get(key)
                if g is None:
                    merged[key] = [reason, masked, n, 1, rel, line, ctx]
                    continue
                g[2] += n
                g[3] += 1
                if WEIGHT[reason] > WEIGHT[g[0]]:
                    g[0] = reason
                if (rel, line) < (g[4], g[5]):  # the first place in path order, whatever order the workers finish in
                    g[4], g[5], g[6] = rel, line, ctx
    rows = sorted(merged.values(), key=lambda g: (-WEIGHT[g[0]], -g[3], -g[2], g[4], g[5]))
    for reason, masked, hits, nfiles, rel, line, ctx in rows[:top or None]:
        print(f'{reason:9} {masked}  hits={hits} files={nfiles}  {rel}:{line}')
        if context:
            print(f'          context: {ctx!r}')
    counts = {r: sum(1 for g in rows if g[0] == r) for r in WEIGHT}
    print(f'{len(files)} files read, {len(rows)} groups'
          + ''.join(f', {r}: {counts[r]}' for r in WEIGHT) + (f' (showing {top})' if top else ''))
    return 0
