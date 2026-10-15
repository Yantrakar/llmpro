"""Tests for false positives in the redactor (redact.py) and the secret check (check.py).

Run: pytest tests/test_redaction.py

The 2026-10-15 regression: a chat that only mentioned a private-key header (no END line) made the
redactor treat the rest of the chat as a key, learn every line of it, and sweep table rows and code
out of every other file. Key text here is built at run time, so no literal key block sits in this
file or in a chat log that shows it.
"""
import base64
import contextlib
import hashlib
import io
import os
import re
import sys
import unittest

from agentspro import check as secretcheck
from agentspro import redact as common
from agentspro import store

DASH = '-' * 5
BEGIN = f'{DASH}BEGIN PRIVATE KEY{DASH}'
END = f'{DASH}END PRIVATE KEY{DASH}'
BODY = [base64.b64encode(hashlib.sha256(s).digest()).decode() for s in (b'one', b'two')]
PROSE = '| a | b |\n|---|---|---|\nsteps:\nif a.matrix:\n## Summary Protocol\nfrom x import ProcessPoolExecutor\n'


class Redact(unittest.TestCase):
    def setUp(self):
        self.saved = dict(common.LEARNED), dict(common.LEARNED_BY)
        common.LEARNED.clear()

    def tearDown(self):
        common.LEARNED.clear()
        common.LEARNED.update(self.saved[0])
        common.LEARNED_BY.clear()
        common.LEARNED_BY.update(self.saved[1])

    def test_header_without_end_keeps_the_rest(self):
        out = common.redact(f'the guard looks for {BEGIN}\n{PROSE}')
        self.assertTrue(out.endswith(PROSE), out)
        for line in PROSE.splitlines():
            self.assertNotIn(line, common.LEARNED)

    def test_block_with_end_is_redacted_and_learns_only_the_body(self):
        out = common.redact(f'before\n{BEGIN}\n' + '\n'.join(BODY) + f'\n{END}\nafter {PROSE}')
        self.assertIn('[REDACTED:private-key]', out)
        for line in BODY:
            self.assertNotIn(line, out)
        self.assertTrue(out.endswith(f'after {PROSE}'), out)
        self.assertEqual(set(common.LEARNED), set(BODY))

    def test_json_escaped_block(self):
        out = common.redact('"' + BEGIN + '\\n' + '\\n'.join(BODY) + '\\n' + END + '\\n"')
        self.assertEqual(out, '"[REDACTED:private-key]\\n"')

    def test_sweep_leaves_prose_alone(self):
        common.redact(f'{BEGIN}\n{PROSE}')
        self.assertEqual(common.sweep(PROSE), PROSE)

    def test_api_ids_are_not_secrets(self):
        text = 'tool key result: toolu_01QwErTy8uIoPaSdFgHjKlZx and chatcmpl-AbC12dEf34GhI56jKl'
        self.assertEqual(common.redact(text), text)
        self.assertEqual(common.LEARNED, {})

    def test_new_rules_leave_lookalikes_alone(self):
        # 2026-10-15 rules for pw:, use X to log in, table cells and mysql -p: none of these is a secret
        for text in ["grep -rnoiE 'password|passwd|pw_hash9|token' src", 'ssh -p 2222 host', 'mysql -u root -p mydb',
                     'use id_ed25519 to log in', 'use admin to log in', '| token | string |', '| password | Yes |',
                     'pw: 3', '| pw | ~/.ssh/id_x |']:
            self.assertEqual(common.redact(text), text)

    def test_ordinary_strings_are_not_learned(self):
        # 2026-10-15: learned dates and numbers were swept out of timestamps
        for v in ['2026-10-15', '2026-10-15T12:28:00Z', '12345678', '1.2.3-rc1', 'v10.4.2']:
            common.learn(v, sure=True)
        self.assertEqual(common.LEARNED, {})

    def test_quoted_password_shaped_like_a_name_is_learned(self):
        # redacted but not learned, a copy elsewhere stayed and failed the leak check
        v = 'Harbor' + str(7731) + 'Lantern'  # built at run time: no password-shaped literal in this file
        out = common.redact(f'password: "{v}"')
        self.assertEqual(out, 'password: "[REDACTED:password]"')
        self.assertIn(v, common.LEARNED)
        for name in ('HarborLantern', 'llm-node' + str(7), 'plain_text_' + str(2)):
            common.learn(name, sure=True)  # a word or a kebab/snake name: never swept
            self.assertNotIn(name, common.LEARNED)
        common.learn('Note' + str(2026), sure=False)
        self.assertNotIn('Note2026', common.LEARNED)

    def test_redaction_is_stable(self):
        # 2026-10-15: a marker is shorter than its value, so a second pass reached the next line's token
        vals = [base64.urlsafe_b64encode(hashlib.sha256(s).digest()).decode().rstrip('=') for s in (b'a', b'b')]
        once = common.redact(f'> print(auth.ensure_bootstrap_token())\n> {vals[0]}\n> {vals[1]}\n')
        self.assertEqual(once, '> print(auth.ensure_bootstrap_token())\n> [REDACTED:token]\n> [REDACTED:token]\n')
        self.assertEqual(common.redact(once), once)

    def test_second_machine_lookalikes_are_not_learned(self):
        # 2026-10-15, a second machine's first sync: a key path in a shell default, a C++ name and timestamps were learned
        code = 'pos' + 'Cmd12' + 'ReadyFlag'
        for text in ['PUBKEY=${PUBKEY:-$HOME/.ssh/id_ed25519.pub}', f'static EcToken statToken = {code};',
                     'measurement tokens: 20260812T143000Z.jsonl', 'auth runtime/20260812T143000123456Z-check']:
            self.assertEqual(common.redact(text), text)
            self.assertEqual([h for h in secretcheck.scan_patterns(text) if h[0] == 'near-keyword'], [])
        self.assertEqual(common.LEARNED, {})
        v = 'Harbor' + str(7731) + 'Lantern'
        self.assertEqual(common.redact(f'token = "{v}"'), 'token = "[REDACTED:token]"')  # quoted: still a secret

    def test_hex_ids_near_keywords_are_kept(self):
        for n in (16, 17, 40):
            v = hashlib.sha256(str(n).encode()).hexdigest()[:n]
            self.assertEqual(common.redact(f'token {v}'), f'token {v}')
            self.assertEqual([h for h in secretcheck.scan_patterns(f'token {v}') if h[0] == 'near-keyword'], [])
        v = hashlib.sha256(b'k').hexdigest()
        self.assertEqual(common.redact(f'token {v}'), 'token [REDACTED:token]')


class Store(unittest.TestCase):
    def test_frontmatter_after_blank_lines(self):
        fields, body = common.parse_frontmatter('\n---\nname: a\ndescription: b c\n---\n\nfact\n')
        self.assertEqual((fields.get('name'), fields.get('description'), body), ('a', 'b c', 'fact\n'))

    def test_write_text_replaces_whole_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'a', 'b.md')
            common.write_text(p, 'one')
            common.write_text(p, 'two')
            with open(p) as fh:
                self.assertEqual(fh.read(), 'two')
            self.assertEqual(os.listdir(os.path.dirname(p)), ['b.md'])

    def test_local_only_claude_slug(self):
        from agentspro.importer import memory as import_memory
        saved = store.LOCAL_ONLY[:]
        try:
            store.LOCAL_ONLY[:] = ['/home/u/code/private']
            self.assertTrue(import_memory.claude_local_only('-home-u-code-private'))
            self.assertTrue(import_memory.claude_local_only('-home-u-code-private-web'))
            self.assertFalse(import_memory.claude_local_only('-home-u-code-private2'))
        finally:
            store.LOCAL_ONLY[:] = saved


class SecretcheckCollector(unittest.TestCase):
    def collected(self, text):
        return [line for m in secretcheck.KEY_BLOCK.finditer(text)
                for line in re.split(secretcheck.KEY_SEP, m.group(1)) if secretcheck.KEY_LINE.fullmatch(line)]

    def test_block_with_end(self):
        self.assertEqual(self.collected(f'{BEGIN}\n' + '\n'.join(BODY) + f'\n{END}'), BODY)

    def test_header_and_end_far_apart_collect_nothing(self):
        self.assertEqual(self.collected(f'{BEGIN}\n{PROSE}{END}'), [])

    def test_marker_cut_by_the_keyword_window_is_no_keyword(self):
        hexval = hashlib.sha256(b'digest').hexdigest()[:16]
        text = f"'a': ('[REDACTED:token]', None), 'some-ansible-deploy': ('{hexval}', None)"
        self.assertEqual([h for h in secretcheck.scan_patterns(text) if h[0] == 'near-keyword'], [])


class SecretcheckRules(unittest.TestCase):
    def test_json_key_must_end_in_a_secret_name(self):
        self.assertFalse(secretcheck.secret_field('staticTokens', 'python3'))
        self.assertFalse(secretcheck.secret_field('tokenizer', 'cl100k_base'))
        self.assertTrue(secretcheck.secret_field('password', 'Zq8vL2mWp'))
        self.assertTrue(secretcheck.secret_field('apiKey', 'abc123def456'))

    def test_public_keys_and_api_ids_are_no_hits(self):
        body = base64.b64encode(b'\0\0\0\x0bssh-ed25519' + hashlib.sha256(b'pub').digest()).decode()
        text = f'auth key: toolu_01QwErTy8uIoPaSdFgHjKlZx, authorized key ssh-ed25519 {body} me@host'
        self.assertEqual(secretcheck.scan_patterns(text), [])

    def test_report_groups_hits_by_value(self):
        out = io.StringIO()
        rows = [('a.md', 1, 'whole [token] x…', ''), ('b.md', 2, 'whole [token] x…', ''), ('b.md', 3, 'whole [token] x…', '')]
        with contextlib.redirect_stdout(out):
            self.assertEqual(secretcheck.print_leaks(rows, False), 3)
        self.assertEqual(out.getvalue(), 'LEAK a.md:1 whole [token] x…   (+2 more, 2 files in all)\n')


if __name__ == '__main__':
    unittest.main()
