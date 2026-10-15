"""Copy instruction files into the store.

  instructions/<machine>/<tool>/<original path, home folder as HOME>   (not "~": a folder named ~ is one
                                                                     typo away from `rm -rf ~`)

Skills are not copied: ~/.agents/skills is the only place a skill lives (the user, 2026-10-09: anywhere
else is garbage). Every tool reads it or links to it. Each one is recorded in the manifest as lives-here.

Text files go through redact(). Everything else is copied byte for byte. Each item is recorded in
manifest.jsonl, and so is each source that was looked at and left out on purpose, with the reason.
Sources are only read. Re-runs rewrite changed files and remove nothing.

Besides the files every machine has, more can be named in the store's config.toml:
  [instructions] extra        = [["codex", "~/project/AGENTS.md"], ...]    (tool, file)
  [instructions] extra_globs  = [["claude-code", "~/project/.claude/plans/*.md"], ...]    (tool, pattern)
  [instructions] skip         = [["codex", "instructions", "~/project/.ai/logs", "why"], ...]    (tool, kind, path, reason)
"""
import glob
import os
import re
import sys

from .. import store as cfg
from ..redact import MACHINE, is_local_only, load_manifest, manifest_entry, redact, save_manifest, write_text
from ..store import home


def h(p):
    return os.path.join(home(), p)


def expand(path):
    return os.path.expanduser(path)


def instructions():  # (tool, path)
    return [
        ('codex', h('AGENTS.md')),
        ('claude-code', h('.claude/CLAUDE.md')),
        *[('claude-code', p) for p in sorted(glob.glob(h('.claude/plans/*.md')))],
        *[(tool, expand(path)) for tool, path in cfg.config_list('instructions', 'extra')],
        *[(tool, p) for tool, pattern in cfg.config_list('instructions', 'extra_globs') for p in sorted(glob.glob(expand(pattern)))],
    ]


def opencode_config():
    return h('.config/opencode/opencode.jsonc')  # only its "instructions" key is taken


def skipped():  # (tool, kind, path, reason)
    return [
        ('claude-code', 'skill', h('.agents/skills/synced'), 'skills synced from claude.ai by Claude, not user-written'),
        ('codex', 'skill', h('.codex/skills/.system'), 'bundled Codex system skills'),
        ('claude-code', 'config', h('.claude/settings.json'), 'tool config (settings*.json), not instructions'),
        ('codex', 'config', h('.codex/rules/default.rules'), 'permission config, not instructions'),
        ('copilot', 'config', h('.config/Code/User/settings.json'), 'tool config, not instructions'),
        ('openclaw', 'skill', h('.openclaw'), 'OpenClaw excluded by the user (openclaw-keepsake included)'),
        ('gemini', 'instructions', h('.gemini/GEMINI.md'), 'Gemini not in scope for this import'),
        ('opencode', 'config', h('.config/opencode/setupskills.sh'), 'installer script for the opencode skills, not a skill'),
        *[(tool, kind, expand(path), reason) for tool, kind, path, reason in cfg.config_list('instructions', 'skip')],
    ]


TEXT_EXT = {'.md', '.txt', '.py', '.sh', '.json', '.jsonc', '.toml', '.yaml', '.yml', '.cfg', '.ini', ''}


def tilde(path):
    return '~' + path[len(home()):] if path.startswith(home() + os.sep) else path


def store_rel(path):
    return 'HOME' + path[len(home()):] if path.startswith(home() + os.sep) else path.lstrip('/')


def copy(src, dst):
    """-> True if dst changed."""
    data = open(src, 'rb').read()
    if os.path.splitext(src)[1].lower() in TEXT_EXT:
        try:
            data = redact(data.decode('utf-8')).encode('utf-8')
        except UnicodeDecodeError:
            pass
    if os.path.exists(dst) and open(dst, 'rb').read() == data:
        return False
    write_text(dst, data, binary=True)
    return True


def opencode_instructions():
    text = open(opencode_config(), encoding='utf-8').read()
    m = re.search(r'"instructions"\s*:\s*\[(.*?)\]', text, re.S)
    if not m:
        return None
    items = re.findall(r'"((?:[^"\\]|\\.)*)"', re.sub(r'//[^\n]*', '', m.group(1)))
    return ('# opencode instructions\n\nThe "instructions" entries from ~/.config/opencode/opencode.jsonc. '
            'Only this key is copied; the rest of the file is tool config.\n\n' + ''.join(f'- {i}\n' for i in items))


def run(store):
    manifest = load_manifest(store)
    changed = 0

    for tool, src in instructions():
        if not os.path.isfile(src):
            continue
        if is_local_only(src):  # a local-only project (config.toml [privacy]): never copied into the store
            manifest[(tool, MACHINE, 'instructions', tilde(src))] = manifest_entry(tool, 'instructions', src, tilde(src), '', 'skipped:local-only')
            continue
        rel = os.path.join('instructions', MACHINE, tool, store_rel(src))
        changed += copy(src, os.path.join(store, rel))
        manifest[(tool, MACHINE, 'instructions', tilde(src))] = manifest_entry(tool, 'instructions', src, tilde(src), rel, 'imported')

    OPENCODE_CONFIG = opencode_config()
    if os.path.isfile(OPENCODE_CONFIG):
        text = opencode_instructions()
        oid = tilde(OPENCODE_CONFIG) + '#instructions'
        if text:
            rel = os.path.join('instructions', MACHINE, 'opencode', store_rel(OPENCODE_CONFIG) + '.instructions.md')
            dst = os.path.join(store, rel)
            text = redact(text)
            if not os.path.exists(dst) or open(dst, encoding='utf-8').read() != text:
                write_text(dst, text)
                changed += 1
            manifest[('opencode', MACHINE, 'instructions', oid)] = manifest_entry('opencode', 'instructions', OPENCODE_CONFIG, oid, rel, 'imported')

    skills = os.path.join(store, 'skills')
    for name in sorted(os.listdir(skills)) if os.path.isdir(skills) else []:
        if os.path.isfile(os.path.join(skills, name, 'SKILL.md')):
            manifest[('agents', MACHINE, 'skill', name)] = manifest_entry(
                'agents', 'skill', os.path.join(home(), '.agents', 'skills', name), name, os.path.join('skills', name), 'skipped:lives-here')

    for tool, kind, path, reason in skipped():
        manifest[(tool, MACHINE, kind, tilde(path))] = manifest_entry(tool, kind, path, tilde(path), '', f'skipped:{reason}')

    save_manifest(store, manifest)
    print(f'{changed} files written', file=sys.stderr)
