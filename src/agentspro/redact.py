"""Redaction, the sweep, and the conversation model the importers share. Standard library only."""
import datetime
import getpass
import json
import os
import re
import sys
import time
import unicodedata

from . import store
from .store import MACHINE, config_paths, is_local_only  # noqa: F401

_START = time.monotonic()


def step(msg):
    """A progress line with the seconds since start, flushed so it shows at once when sync is piped."""
    if not store.QUIET[0]:
        print(f'[{time.monotonic() - _START:6.1f}s] {msg}', file=sys.stderr, flush=True)


def apply_config():
    """Words never swept: this person's login name and this machine's name (added at run time), and
    [redact] never_sweep from the store's config.toml. Call it after store.configure()."""
    extra = {getpass.getuser(), MACHINE, *store.config_list('redact', 'never_sweep')}
    _NO_SWEEP.update(w.lower() for w in extra if isinstance(w, str) and w)


def write_text(path, text, binary=False):
    """Write a file whole or not at all: a killed run or a second one never leaves half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.tmp{os.getpid()}'
    with open(tmp, 'wb' if binary else 'w', **({} if binary else {'encoding': 'utf-8'})) as fh:
        fh.write(text)
    os.replace(tmp, path)

# ---------------------------------------------------------------- redaction

# A key block: the BEGIN line, then only key-shaped lines (base64, or "Proc-Type: 4,ENCRYPTED" headers),
# split by newlines, spaces or a JSON string's \n. With no END line (a chat that only names the header),
# only the header and the base64 lines right after it: never the rest of the text (2026-10-15: that ate
# Markdown tables and code, and learn() then swept those lines out of the skills).
_KEY_SEP = r'(?:\s|\\+[rn]|>)+'  # ">": a key quoted in a chat ("> " before each line)
_KEY_B64 = re.compile(r'[A-Za-z0-9+/=]{16,}')
_PRIVATE_KEY = re.compile(
    r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----'
    rf'(?:(?:{_KEY_SEP}(?:[A-Za-z0-9+/=]+|[A-Za-z][A-Za-z-]*: ?[A-Za-z0-9,\-]+))*{_KEY_SEP}?-----END [A-Z0-9 ]*PRIVATE KEY-----'
    rf'|(?:{_KEY_SEP}{_KEY_B64.pattern})*)')

_SECRET_PATTERNS = [
    ('private-key', _PRIVATE_KEY),
    ('anthropic-key', re.compile(r'\bsk-ant-[A-Za-z0-9_\-]{20,}')),
    ('openai-key', re.compile(r'\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}')),
    ('github-token', re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})')),
    ('gitlab-token', re.compile(r'\bglpat-[A-Za-z0-9_\-]{20,}')),
    ('aws-key', re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')),
    ('slack-token', re.compile(r'\bxox[abposr]-[A-Za-z0-9-]{10,}')),
    ('google-key', re.compile(r'\bAIza[0-9A-Za-z_\-]{8,}')),  # also catches keys cut short by output truncation
    ('tailscale-key', re.compile(r'\btskey-[a-z]+-[A-Za-z0-9\-]{6,}')),
    ('huggingface-token', re.compile(r'\bhf_[A-Za-z0-9]{30,}')),
    ('jwt', re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]*)?')),
]
_AUTH_HEADER = re.compile(r'(?i)(\bauthorization\s*["\']?\s*[:=]\s*["\']?(?:bearer|basic|token)\s+)([A-Za-z0-9._~+/=\-]{8,})')
_BEARER = re.compile(r'(?i)(\bbearer\s+)([A-Za-z0-9._~+/=\-]{20,})')
_URL_CREDS = re.compile(r'(\b[a-z][a-z0-9+.\-]*://[^/\s:@"\']+:)([^/\s@"\']+)(@)')
_ASSIGN = re.compile(
    r'(?i)(\b[A-Za-z0-9_\-]*(password|passwd|passphrase|pass(?=\\?["\'\s:=])|pwd|masterauth|secret|token|api[_\-]?key|apikey'
    r'|access[_\-]?key|auth[_\-]?key|authkey|credentials?|salt|key(?=\\?["\'\s:=]))'
    r'[A-Za-z0-9_\-]*\\?["\']?[ \t]*[:=][ \t]*)(\\?["\'`]|)([^\s"\'`,;&(){}<>\[\]\\]{4,})(\3)')
_CLI_PASS = re.compile(r'(--(?:password|passwd|pass|pw)(?:=|[ \t]+))(["\']?)([^\s"\'-][^\s"\']{3,})(\2)')
_CLI_TOKEN = re.compile(r'(--(?:token|api[_-]?key|secret|auth[_-]?key|access[_-]?key)(?:=|[ \t]+))(["\']?)([A-Za-z0-9._~+/=\-]{12,})(\2)')
# Query parameters in URLs that carry keys, tokens, signatures or OAuth state.
_URL_PARAM = re.compile(r'(?i)([?&][a-z_]*(?:key|token|secret|signature|sig|state|auth)=)([^&\s"\'#<>\[\]]{12,})')
_PROSE_PASS = re.compile(r'(?i)(\b(?:password(?:[ \t]+is)?[ \t]+|(?:passwd|pw)(?:[ \t]+is[ \t]+|[ \t]*[:=][ \t]*)))([`"\']?)([^\s`"\'.,;:)]{4,})(\2)')
# "use X to log in": a value with a digit (or quoted) right before a login phrase.
_PROSE_USE = re.compile(r'(?i)(\buse[ \t]+)([`"\']?)([^\s`"\']{6,})(\2)'
                        r'(?=[ \t]+(?:to|for|when)[ \t]+(?:log[ \t-]?in|login|sign[ \t-]?in|signin|authenticate))')
# A Markdown table row that names the secret in one cell and gives it in the next: | password | X |.
_TABLE_CELL = re.compile(r'(?im)(\|[ \t]*\**[A-Za-z _\-]{0,20}?\b(password|passwd|passphrase|pw|pwd|secret|token'
                         r'|api[ _\-]?key|access[ _\-]?key)\**[ \t]*\|[ \t]*)(`?)([^\s|`]{4,})(\3)(?=[ \t]*\|)')
# The mysql family takes -pX or -p X (and redis-cli -a X); a bare -p elsewhere is a port or a flag.
_CLI_P = re.compile(r'(\b(?:mysql|mariadb|mysqldump|mysqladmin|mysqlimport|mysqlshow|mysqlcheck)\b[^\n|;&]*?[ \t]-p[ \t]*'
                    r'|\bredis-cli\b[^\n|;&]*?[ \t]-a[ \t]+)([\'"]?)([^\s\'"\-][^\s\'"]{3,})(\2)')
# Passwords found by the patterns above are also swept as literals from every file in the same run,
# so prose mentions ("the default is X") go too. Kept in memory only. Common words are never swept.
LEARNED = {}  # value -> kind
LEARNED_BY = {}  # value -> the redactor rule that learned it, for the report in importer/all.py
_NO_SWEEP = {'postgres', 'password', 'passwd', 'secret', 'admin', 'administrator', 'ubuntu', 'redis', 'mysql',
             'changeme', 'default', 'required', 'example', 'docker', 'string'}
_SSHPASS = re.compile(r'(\bsshpass[ \t]+-p[ \t]*)(["\']?)([^\s"\'`]{4,})(\2)')
# A shell default or expansion names where a secret is, never is one: ${KEY:-$HOME/.ssh/id_ed25519.pub} (2026-10-15)
_COMPACT_TIME = re.compile(r'\d{8}T\d{6,12}Z?(?![A-Za-z0-9])')  # 20260812T143000Z in file names (2026-10-15)
_SHELL_REF = re.compile(r'[-:=]?\$\{?[A-Za-z_]')
_PLACEHOLDER = re.compile(r'(?i)^(\$|%|your|xxx|\*{3}|redacted|none|null|true|false|changeme|example|placeholder|dummy|test|os\.|process\.|env\.|self\.|this\.|config\.|settings\.)')


def _code_identifier(v):
    """A camelCase name in code: words or abbreviations (Cmd) of three or more letters, each capitalised,
    and at most a short number. A generated token changes case every letter or two."""
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', v) or not re.search(r'[a-z][A-Z]', v):
        return False
    segs = re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+', v)
    digits = [g for g in segs if g.isdigit()]
    words = [g for g in segs if g.isalpha()]
    return (sum(map(len, digits)) <= 3 and all(len(g) >= 3 and (len(g) <= 4 or re.search('[aeiouyAEIOUY]', g)) for g in words))


def _assign_repl(m):
    key, kind, quote, value = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
    if (value.startswith('[REDACTED') or _PLACEHOLDER.match(value) or _SHELL_REF.match(value)
            or _COMPACT_TIME.match(value)):
        return m.group(0)
    if kind == 'pwd' and not (quote or re.search(r'\d', value)) or value.startswith(('~', '/', './')):
        return m.group(0)  # $PWD, pwd commands, paths
    if kind == 'pass' and not quote:
        return m.group(0)  # "PASS: test name" in test output
    if key.rstrip('"\' \t:=').upper().endswith('NOPASSWD') or (not quote and value.startswith('/')):
        return m.group(0)  # sudoers rule, or a path
    if quote:
        looks_secret = True  # any quoted literal, including plain words like "hunter"
    elif kind.startswith('pass'):
        looks_secret = len(value) >= 6 and not re.search(r'[.]', value)  # unquoted YAML/TOML/CLI passwords
    else:
        # letters and digits, and not a snake_case/dotted name such as max_tokens2 or cfg.token1
        looks_secret = (re.search(r'[A-Za-z]', value) and re.search(r'\d', value) and len(value) >= 8
                        and not re.fullmatch(r'[a-z_][a-z0-9_.]*', value) or len(value) >= 20 and '.' not in value
                        and re.search(r'\d', value) and not re.search(r'[()]', value))
    if not looks_secret:
        return m.group(0)
    if kind == 'key' and not _looks_generated(value):
        return m.group(0)  # "key" also names map keys, keyboard keys and file paths: only generated values
    if (kind == 'salt' or kind.startswith('credential')) and not (_looks_generated(value) or _mixed_secret(value)):
        return m.group(0)
    kind = 'password' if kind.startswith('pass') or kind in ('masterauth', 'pwd') else kind.replace('_', '-').replace('apikey', 'api-key')
    if not quote and kind != 'password' and _code_identifier(value):
        return m.group(0)  # C++ code: statToken = posCmd12Ready (2026-10-15)
    learn(value, kind, sure=bool(quote) or kind == 'password')
    return f'{key}{quote}[REDACTED:{kind}]{quote}'


_PUBLIC_START = re.compile(r'^(?:sk-ant-|sk-(?:proj-|svcacct-|admin-)?|gh[pousr]_|github_pat_|glpat-|xox[a-z]-|AKIA|ASIA|AIza'
                           r'|hf_|tskey-[a-z]+-|npm_)')


def _classes(v):
    return (any(c.islower() for c in v) + any(c.isupper() for c in v) + any(c.isdigit() for c in v)
            + any(not c.isalnum() for c in v))


# Dates, numbers and versions: sweeping one would cut it out of every timestamp and changelog (2026-10-15).
_ORDINARY = re.compile(r'[\d\s.:\-/+TZ]+|v?\d+(?:\.\d+)+(?:[\-+][0-9A-Za-z.\-]+)?')


def learn(value, kind='password', sure=False):
    """Remember a secret so sweep() can remove its other copies. A plain word or name is never learned:
    sweeping it would cut ordinary prose. sure: a password or token rule redacted it, so a CamelCase value
    with digits is learned too, as check.py collects it; else the leak check finds
    its other copies. Kebab and snake names with digits (llm-node1, plain_text_2) stay unlearned."""
    value = value.strip()
    if len(value) < 6 or value.lower() in _NO_SWEEP or value.startswith(('[REDACTED', '$', '{', '<')):
        return
    if _ORDINARY.fullmatch(value) or _PUBLIC_ID.fullmatch(value):
        return
    if _SHELL_REF.match(value):
        return
    if _identifier(value) and not _looks_generated(value) and not (sure and _camel_with_digits(value)):
        return
    if value not in LEARNED:
        LEARNED[value] = kind
        LEARNED_BY[value] = sys._getframe(1).f_code.co_name


def _identifier(v):
    """Shaped like a word, a name or a path rather than a secret: ARC_FOO, snake_case, kebab-case, dotted.name,
    CamelCase, a path, a flag, a URL or a file name."""
    return bool(re.fullmatch(r'[A-Za-z]+|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+|[a-z][a-z0-9]*(?:[_.\-][a-z0-9]+)+'
                             r'|(?:[A-Z][a-z0-9]+)+|[a-z]+(?:[A-Z][a-z0-9]+)+', v) or _path_like(v))


def _camel_with_digits(v):
    return bool(re.search(r'\d', v) and re.search(r'[A-Z]', v) and re.search(r'[a-z]', v) and not _path_like(v))


def _path_like(v):
    """A path, a flag, a URL or a file name."""
    return bool(v.startswith(('~', '/', './', '-', 'http:', 'https:')) or re.search(r'\.[a-z]{2,4}$', v))


def _mixed_secret(v):
    """Letters and digits mixed, 8+ characters, not shaped like a name or a version."""
    return (len(v) >= 8 and re.search(r'[A-Za-z]', v) and re.search(r'\d', v)
            and not _identifier(v) and not re.fullmatch(r'v?\d+(?:[.\-]\w+)*', v))


def _piece_worth_sweeping(piece):
    flips = sum(1 for a, b in zip(piece, piece[1:]) if a.isalpha() and b.isalpha() and a.isupper() != b.isupper())
    return (re.search(r'[A-Za-z]', piece) and re.search(r'\d', piece)) or flips >= 3


_PIECES_CACHE = {}
SWEEP_COUNT, PIECE_COUNT = {}, {}  # for the report in importer/all.py


def _pieces(values):
    """8-character pieces of each learned value (after its public start, like glpat-) -> kind."""
    key = (id(values), len(values))
    if key not in _PIECES_CACHE:
        out = {}
        for v in values:
            body = v[_PUBLIC_START.match(v).end():] if _PUBLIC_START.match(v) else v
            for i in range(len(body) - 7):
                piece = body[i:i + 8]
                if _piece_worth_sweeping(piece):
                    out.setdefault(piece, LEARNED.get(v, 'password') if isinstance(values, set) else values[v])
        _PIECES_CACHE.clear()
        _PIECES_CACHE[key] = out
    return _PIECES_CACHE[key]


_SWEEP_RUN = re.compile(r'[^\s"\'`<>()\[\]{},;|]{8,}')


def sweep(text, hits=None, values=None):
    """Redact every copy of a secret learned earlier in this run: whole copies, and any piece of 8 or more
    characters of one (a truncated copy, or one glued to other text)."""
    values = LEARNED if values is None else values
    for value in sorted(values, key=len, reverse=True):
        if value in text:
            kind = values[value] if isinstance(values, dict) else LEARNED.get(value, 'password')
            text, n = re.subn(r'(?<![A-Za-z0-9_])' + re.escape(value) + r'(?![A-Za-z0-9_])', f'[REDACTED:{kind}]', text)
            if n:
                SWEEP_COUNT[value] = SWEEP_COUNT.get(value, 0) + n
                if hits is not None:
                    hits.append(kind)
    pieces = _pieces(values)
    if not pieces:
        return text
    spans = []
    for m in _SWEEP_RUN.finditer(text):
        run, base = m.group(0), m.start()
        start = end = None
        for j in range(len(run) - 7):
            kind = pieces.get(run[j:j + 8])
            if kind is not None:
                if start is not None and j <= end:
                    end = j + 8
                else:
                    if start is not None:
                        spans.append((base + start, base + end, k0))
                    start, end, k0 = j, j + 8, kind
                    PIECE_COUNT[run[j:j + 8]] = PIECE_COUNT.get(run[j:j + 8], 0) + 1
        if start is not None:
            spans.append((base + start, base + end, k0))
    for a, b, kind in reversed(spans):
        if text[a:b].startswith('REDACTED:'):
            continue
        text = text[:a] + f'[REDACTED:{kind}]' + text[b:]
        if hits is not None:
            hits.append(kind)
    return text


# A long random-looking string within 40 characters after one of these words is a secret, whatever the
# syntax around it (prose, JSON with escaped quotes, a token printed on the next line).
_NEAR_WORD = re.compile(r'(?i)key|secret|token|pass|auth|cred|salt|pwd')
_NEAR_VALUE = re.compile(r'(?<![A-Za-z0-9_\-+/.])[A-Za-z0-9][A-Za-z0-9_\-+]{15,}=*(?![A-Za-z0-9_\-+/.])')
# Public material that sits next to those words: key fingerprints, public keys, hashes.
_PUBLIC_BEFORE = re.compile(r'(?i)(?:sha(?:1|256|384|512)|md5)[:=]\s*$|(?:ssh-(?:rsa|ed25519|dss)|ecdsa-sha2-\w+|sk-ssh-\S+)\s+$'
                            r'|(?:commit|digest|checksum|hash|tree|blob|rev|head|@sha256)[\s:=@\-`"\']{0,3}$')
# A value given to a field that names or points at something is an id, not a secret: project_id:"app-…".
_NAMED_FIELD = re.compile(r'(?i)(?<![A-Za-z])(?:[a-z]+_)*(?:id|ids|name|uuid|path|file|url|model|project|repo|ref|sha|hash)'
                          r'\\?["\']?[ \t]*[:=][ \t]*\\?["\']?$')
# Ids that APIs give to tool calls, messages, requests and completions. Learning one swept its prefix
# ("toolu_01") out of every other id in the store (2026-10-15).
_PUBLIC_ID = re.compile(r'(?:toolu|srvtoolu|msg|req|call|resp|chatcmpl|file|batch)[_\-][A-Za-z0-9]{8,}')
_PUBLIC_BLOCK = re.compile(r'-----BEGIN PGP PUBLIC KEY BLOCK-----.*?(?:-----END PGP PUBLIC KEY BLOCK-----|\Z)', re.S)


def _looks_generated(token):
    """Letters and digits mixed the way a generator mixes them, not the way words and names are."""
    core = re.sub(r'[^A-Za-z0-9]', '', token)
    if len(core) < 16 or core.isdigit() or re.fullmatch(r'0x[0-9a-fA-F]+', token) or len(set(core)) < 6:
        return False
    if _COMPACT_TIME.fullmatch(core):
        return False
    segments = re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+', core)
    if not re.search(r'\d', core):
        return len(core) / len(segments) <= 2.2  # no digits: only if the case flips every couple of letters

    def wordy(seg):
        low = seg.lower()
        consonant_run = max((len(r) for r in re.findall(r'[^aeiouy]+', low)), default=0)
        return len(seg) >= 4 and consonant_run <= 5 and sum(c in 'aeiouy' for c in low) >= 0.15 * len(low)
    parts = [p for p in re.split(r'[_\-.:/]', token) if p]
    if len(parts) >= 3 and all(len(p) <= 5 or p.isdigit() or p.isalpha() and wordy(p) for p in parts):
        return False  # a name built from short parts: qwen3_30b_a3b_instruct_2507
    return sum(len(seg) for seg in segments if seg.isalpha() and wordy(seg)) < 0.6 * len(core)


def _near_keyword(text, note):
    blocks = [(m.start(), m.end()) for m in _PUBLIC_BLOCK.finditer(text)]
    out, last = [], 0
    for m in _NEAR_VALUE.finditer(text):
        a = m.start()
        window = text[max(0, a - 40):a]
        if not _NEAR_WORD.search(window) or m.group(0).startswith('REDACTED'):
            continue
        runs = [r for r in re.findall(r'[A-Za-z0-9]+', m.group(0)) if len(r) >= 16]
        if not runs or not any(_looks_generated(r) for r in runs):
            continue  # names, UUIDs and file names: no long random run
        if (not _looks_generated(m.group(0)) or _PUBLIC_BEFORE.search(text[max(0, a - 24):a])
                or _code_identifier(m.group(0))):
            continue
        if re.fullmatch(r'[0-9a-f]+|[0-9A-F]+', m.group(0)) and (len(m.group(0)) < 32 or len(m.group(0)) == 40):
            continue  # hex ids, not secrets: agent ids (17), key fingerprints (16), commit ids (40) (2026-10-15)
        if (any(x <= a < y for x, y in blocks) or _NAMED_FIELD.search(text[max(0, a - 30):a])
                or _PUBLIC_ID.fullmatch(m.group(0))):
            continue
        note('token')
        learn(m.group(0), 'token')
        out.append(text[last:a] + '[REDACTED:token]')
        last = m.end()
    return ''.join(out) + text[last:] if out else text


def redact(text, hits=None):
    """Replace secrets with [REDACTED:<kind>]. `hits` (a list) collects the kinds found.

    Runs until nothing changes: a marker is shorter than the value it replaced, so it can bring a keyword
    within reach of the next value. The result is the same whether a text is redacted once or again later
    (2026-10-15: a sync rewrote the same files on every run).
    """
    for _ in range(5):
        new = _redact_once(text, hits)
        if new == text:
            break
        text = new
    return text


def _redact_once(text, hits):
    if not text:
        return text

    def note(kind):
        if hits is not None:
            hits.append(kind)

    for kind, pat in _SECRET_PATTERNS:
        def sub(m, kind=kind):
            note(kind)
            if kind == 'private-key':
                for line in re.split(_KEY_SEP, m.group(0)):
                    if _KEY_B64.fullmatch(line):  # the base64 lines only: never a header or a word
                        learn(line, kind)
            else:
                learn(m.group(0), kind)
            return f'[REDACTED:{kind}]'
        text = pat.sub(sub, text)

    def hdr(m):
        note('auth-header')
        learn(m.group(2), 'auth-header', sure=True)
        return m.group(1) + '[REDACTED:auth-header]'
    text = _AUTH_HEADER.sub(hdr, text)
    text = _BEARER.sub(hdr, text)

    def url(m):
        note('url-credentials')
        learn(m.group(2), sure=True)
        return m.group(1) + '[REDACTED:password]' + m.group(3)
    text = _URL_CREDS.sub(url, text)

    def assign(m):
        out = _assign_repl(m)
        if out != m.group(0):
            note(m.group(2).lower())
        return out
    text = _ASSIGN.sub(assign, text)

    def sshpass(m):
        if m.group(3).startswith(('$', '[REDACTED')):
            return m.group(0)
        note('password')
        learn(m.group(3), sure=True)
        return f'{m.group(1)}{m.group(2)}[REDACTED:password]{m.group(4)}'
    text = _SSHPASS.sub(sshpass, text)

    def cli(m):
        v = m.group(3)
        if v.startswith(('$', '[REDACTED', '<', '{')) or v.isupper() or _PLACEHOLDER.match(v):
            return m.group(0)
        note('password')
        learn(v, sure=True)
        return f'{m.group(1)}{m.group(2)}[REDACTED:password]{m.group(4)}'
    text = _CLI_PASS.sub(cli, text)

    def cli_token(m):
        v = m.group(3)
        if v.isupper() or _PLACEHOLDER.match(v) or not (re.search(r'\d', v) and re.search(r'[A-Za-z]', v)):
            return m.group(0)
        note('token')
        learn(v, 'token', sure=True)
        return f'{m.group(1)}{m.group(2)}[REDACTED:token]{m.group(4)}'
    text = _CLI_TOKEN.sub(cli_token, text)

    def url_param(m):
        v = m.group(2)
        if not (re.search(r'\d', v) and re.search(r'[A-Za-z]', v)):
            return m.group(0)
        note('url-param')
        learn(v, 'url-param', sure=True)
        return m.group(1) + '[REDACTED:url-param]'
    text = _URL_PARAM.sub(url_param, text)

    def prose(m):
        v = m.group(3)
        if v.startswith(('$', '[REDACTED', '<', '{')) or _PLACEHOLDER.match(v):
            return m.group(0)
        if not m.group(2) and not re.search(r'\d', v):
            return m.group(0)  # "password is required": only quoted values or ones with digits
        note('password')
        learn(v, sure=True)
        return f'{m.group(1)}{m.group(2)}[REDACTED:password]{m.group(4)}'
    text = _PROSE_PASS.sub(prose, text)

    def use(m):
        v = m.group(3)
        if v.startswith(('$', '[REDACTED', '<', '{')) or _PLACEHOLDER.match(v) or re.search(r'[/.]', v):
            return m.group(0)  # a path or a file name: use ~/.ssh/id_x to log in
        if not m.group(2) and not (re.search(r'\d', v) and re.search(r'[A-Za-z]', v)):
            return m.group(0)
        if re.fullmatch(r'[a-z][a-z0-9]*(?:[_\-][a-z0-9]+)+', v):
            return m.group(0)  # a key or account name: id_ed25519
        note('password')
        learn(v, sure=True)
        return f'{m.group(1)}{m.group(2)}[REDACTED:password]{m.group(4)}'
    text = _PROSE_USE.sub(use, text)

    def cell(m):
        v, kind = m.group(4), m.group(2).lower()
        if v.startswith(('$', '[REDACTED', '<', '{', '-')) or _PLACEHOLDER.match(v) or v.isupper():
            return m.group(0)
        s = m.string
        row = s[s.rfind('\n', 0, m.start()) + 1:(s.find('\n', m.end()) + 1 or len(s) + 1) - 1]
        if not re.fullmatch(r'[ \t>]*\|.*\|[ \t]*\r?', row):
            return m.group(0)  # only a Markdown table row: never the | of a grep or regex alternation
        if kind.startswith('p'):  # a password: digits or symbols, not a plain word, name or path
            ok = len(v) >= 6 and re.search(r'[\d\W_]', v) and re.search(r'[A-Za-z]', v) and not _identifier(v)
        else:
            ok = _looks_generated(v) or _mixed_secret(v) and len(v) >= 12
        if not ok:
            return m.group(0)
        kind = 'password' if kind.startswith('p') else 'token'
        note(kind)
        learn(v, kind, sure=True)
        return f'{m.group(1)}{m.group(3)}[REDACTED:{kind}]{m.group(5)}'
    text = _TABLE_CELL.sub(cell, text)

    def cli_p(m):
        v = m.group(3)
        if v.startswith(('$', '[REDACTED', '<', '{')) or _PLACEHOLDER.match(v):
            return m.group(0)
        if not m.group(2) and (v.isalpha() or _identifier(v)):
            return m.group(0)  # mysql -p mydb: -p then a database name, and the password is prompted for
        note('password')
        learn(v, sure=True)
        return f'{m.group(1)}{m.group(2)}[REDACTED:password]{m.group(4)}'
    text = _CLI_P.sub(cli_p, text)
    return _near_keyword(text, note)


# ---------------------------------------------------------------- text helpers

def slugify(text, maxlen=60):
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    text = re.sub(r'[^a-zA-Z0-9]+', '-', text.lower()).strip('-')
    if len(text) > maxlen:
        text = text[:maxlen].rsplit('-', 1)[0] or text[:maxlen]
    return text or 'untitled'


def id8(original_id):
    original_id = re.sub(r'^ses_', '', original_id)  # opencode session ids all start with ses_
    return re.sub(r'[^A-Za-z0-9]', '', original_id)[:8]


def parse_ts(value):
    """ISO string, epoch seconds or epoch ms -> aware datetime in local time (or None)."""
    if value is None or value == '':
        return None
    try:
        if isinstance(value, (int, float)):
            if value > 1e11:
                value = value / 1000
            return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).astimezone()
        s = str(value).replace('Z', '+00:00')
        m = re.match(r'(.*\.\d{6})\d+(.*)', s)  # trim nanoseconds
        if m:
            s = m.group(1) + m.group(2)
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone()
    except (ValueError, OSError, OverflowError):
        return None


def iso(dt):
    return dt.isoformat(timespec='seconds') if dt else ''


def hm(dt):
    return dt.strftime('%Y-%m-%d %H:%M') if dt else ''


_PLAIN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 _./@+\-]*$')


def yaml_value(v):
    if isinstance(v, list):
        return '[' + ', '.join(yaml_value(x) for x in v) + ']'
    if v is None:
        return ''
    v = str(v)
    if _PLAIN.match(v) and not v.endswith(' ') and v.lower() not in ('yes', 'no', 'true', 'false', 'null', 'on', 'off'):
        return v
    return json.dumps(v, ensure_ascii=False)


def frontmatter(fields):
    out = ['---']
    for k, v in fields.items():
        if k == 'summary':
            lines = (v or '').strip().splitlines() or ['']
            out.append('summary: |')
            out.extend('  ' + line for line in lines)
        else:
            out.append(f'{k}: {yaml_value(v)}')
    out.append('---')
    return '\n'.join(out) + '\n'


def read_frontmatter(path):
    """Minimal reader for the frontmatter this package writes (plus Claude memory files)."""
    try:
        text = open(path, encoding='utf-8').read()
    except OSError:
        return {}, ''
    return parse_frontmatter(text)


def parse_frontmatter(text):
    """-> (fields, body) of a file's text. Blank lines before the opening --- are allowed (2026-10-16: a
    memory written with one lost its name and description)."""
    if text.lstrip('\n').startswith('---\n'):
        text = text.lstrip('\n')
    if not text.startswith('---\n'):
        return {}, text
    end = text.find('\n---', 4)
    if end < 0:
        return {}, text
    head, body = text[4:end], text[end + 4:].lstrip('\n')
    fields, key, block = {}, None, []
    for line in head.splitlines():
        if key and (line.startswith('  ') or line == ''):
            block.append(line[2:])
            continue
        if key:
            fields[key] = '\n'.join(block).strip()
            key, block = None, []
        m = re.match(r'^(\s*)([A-Za-z_][\w\-]*):\s*(.*)$', line)
        if not m:
            continue
        indent, k, v = m.groups()
        if v in ('|', '>'):
            key = k
            continue
        if v.startswith('"'):
            try:
                v = json.loads(v)
            except ValueError:
                v = v.strip('"')
        elif v.startswith('[') and v.endswith(']'):
            v = [x.strip().strip('"\'') for x in v[1:-1].split(',') if x.strip()]
        fields[k if not indent else k] = v
    if key:
        fields[key] = '\n'.join(block).strip()
    return fields, body


# ---------------------------------------------------------------- conversation model

class Conversation:
    """Tool-neutral conversation. items: ('msg', role, dt, text) or ('tool', dt, summary, output|None)."""

    def __init__(self, tool, original_id, source_file):
        self.tool = tool
        self.original_id = original_id
        self.source_file = source_file
        self.tool_version = ''
        self.project = ''
        self.title = ''
        self.started = None
        self.updated = None
        self.items = []
        self.skip = None  # reason string when this is not a top-level conversation

    def msg(self, role, dt, text):
        text = (text or '').strip('\n')
        if not text.strip():
            return
        # drop an exact duplicate of the previous message from the same role
        for it in reversed(self.items):
            if it[0] == 'msg':
                if it[1] == role and it[3].strip() == text.strip():
                    return
                break
        self.items.append(('msg', role, dt, text))

    def tool_call(self, dt, summary, output=None):
        self.items.append(['tool', dt, summary, output])
        return self.items[-1]

    @property
    def user_turns(self):
        return sum(1 for it in self.items if it[0] == 'msg' and it[1] == 'user')

    def first_user_text(self):
        for it in self.items:
            if it[0] == 'msg' and it[1] == 'user':
                return it[3]
        return ''


def short_output(text, max_lines=20, max_width=300):
    if text is None:
        return None
    lines = str(text).rstrip('\n').splitlines()
    if not lines:
        return None
    shown = [ln if len(ln) <= max_width else ln[:max_width] + ' …' for ln in lines[:max_lines]]
    if len(lines) > max_lines:
        shown.append(f'… {len(lines) - max_lines} more lines')
    return shown


def inline_cmd(cmd):
    cmd = (cmd or '').strip()
    lines = cmd.splitlines() or ['']
    first = lines[0]
    if len(first) > 240:
        first = first[:240] + ' …'
    extra = f' (+{len(lines) - 1} lines)' if len(lines) > 1 else ''
    tick = '``' if '`' in first else '`'
    pad = ' ' if tick == '``' else ''
    return f'{tick}{pad}{first}{pad}{tick}{extra}'


def render(conv, summary=''):
    hits = []
    fields = {
        'id': f'{conv.tool}:{conv.original_id}',
        'tool': conv.tool,
        'tool_version': conv.tool_version,
        'machine': MACHINE,
        'project': conv.project,
        'title': redact(conv.title, hits),
        'started': iso(conv.started),
        'updated': iso(conv.updated),
        'source_file': conv.source_file,
        'summary': summary or 'TODO: summary not written yet',
    }
    out = [frontmatter(fields)]
    last_role = None
    for it in conv.items:
        if it[0] == 'msg':
            _, role, dt, text = it
            if role != last_role:
                out.append(f'\n### {role} · {hm(dt)}\n')
            else:
                out.append('')
            out.append(redact(text, hits).rstrip() + '\n')
            last_role = role
        else:
            _, dt, summ, output = it
            if last_role != 'assistant':
                last_role = 'assistant'
                out.append(f'\n### assistant · {hm(dt)}\n')
            # the block as it will stand in the file: the store pass redacts the file again and must agree
            block = [f'> tool · {summ}'] + ['> ' + ln if ln else '>' for ln in short_output(output) or []]
            out.append(redact('\n'.join(block), hits) + '\n')
    return '\n'.join(out), hits


def output_path(store, conv):
    dt = conv.started or conv.updated
    year = dt.strftime('%Y') if dt else 'undated'
    day = dt.strftime('%Y-%m-%d') if dt else 'undated'
    name = f'{day}_{slugify(conv.title)}_{id8(conv.original_id)}.md'
    base = os.path.join(store, 'local') if is_local_only(conv.source_file, conv.project) else store
    return os.path.join(base, 'history', MACHINE, conv.tool, year, name)


def file_mtime(path):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
    except OSError:
        return None


# ---------------------------------------------------------------- manifest

def load_manifest(store):
    """Rows of manifest.jsonl and of local/manifest.jsonl (the local-only chats), together."""
    entries = {}
    for path in (os.path.join(store, 'manifest.jsonl'), os.path.join(store, 'local', 'manifest.jsonl')):
        if os.path.exists(path):
            for line in open(path, encoding='utf-8'):
                line = line.strip()
                if line:
                    e = json.loads(line)
                    entries[(e['tool'], e['machine'], e['kind'], e['original_id'])] = e
    return entries


def save_manifest(store, entries):
    """Write the rows back, a local-only row to local/manifest.jsonl and every other row to manifest.jsonl."""
    rows = sorted(entries.values(), key=lambda e: (e['machine'], e['tool'], e['kind'], e['source_path'], e['original_id']))
    local, shared = [], []
    for e in rows:
        (local if e['output'].startswith('local/') or is_local_only(e['source_path']) else shared).append(e)
    for path, part in ((os.path.join(store, 'manifest.jsonl'), shared), (os.path.join(store, 'local', 'manifest.jsonl'), local)):
        if not part and not os.path.exists(path):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.tmp', 'w', encoding='utf-8') as fh:
            for e in part:
                fh.write(json.dumps(e, ensure_ascii=False, sort_keys=False) + '\n')
        os.replace(path + '.tmp', path)


def manifest_entry(tool, kind, source_path, original_id, output, status):
    try:
        st = os.stat(source_path)
        mtime, size = iso(datetime.datetime.fromtimestamp(st.st_mtime).astimezone()), st.st_size
    except OSError:
        mtime, size = '', 0
    return {
        'tool': tool, 'machine': MACHINE, 'kind': kind, 'source_path': source_path,
        'original_id': original_id, 'mtime': mtime, 'size': size,
        'output': output or '', 'status': status,
    }
