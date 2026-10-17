"""Built-in plugin: chats, memories and summaries. The core commands stay in cli.py."""
import os
import shutil
import sys

from .base import Plugin, SetupItem

MARKER = '<!-- installed by llmpro setup; llmpro setup updates it, edits here are overwritten -->'
SKILL_NAME = 'llmpro'


def packaged_skill():
    """The skill folder that ships with llmpro: llmpro/skills/llmpro in an installed copy (the build copies the
    repo's top-level skills/llmpro there), else the top-level folder of a source checkout."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../llmpro
    for path in (os.path.join(here, 'skills', SKILL_NAME),
                 os.path.join(os.path.dirname(os.path.dirname(here)), 'skills', SKILL_NAME)):
        if os.path.isfile(os.path.join(path, 'SKILL.md')):
            return path
    return None


def with_marker(text):
    """SKILL.md with the marker line as the first line of the body (after the front matter)."""
    lines = text.split('\n')
    if lines and lines[0] == '---' and '---' in lines[1:]:
        end = lines.index('---', 1)
        return '\n'.join(lines[:end + 1] + [MARKER, ''] + lines[end + 1:])
    return '\n'.join([MARKER, ''] + lines)


def wanted_files(source):
    """-> {relative path: bytes} for the copy the store should hold."""
    out = {}
    for folder, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        for name in sorted(files):
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, source)
            with open(path, 'rb') as fh:
                data = fh.read()
            if rel == 'SKILL.md':
                data = with_marker(data.decode('utf-8')).encode('utf-8')
            out[rel] = data
    return out


def held_files(dest):
    out = {}
    for folder, dirs, files in os.walk(dest):
        for name in files:
            path = os.path.join(folder, name)
            try:
                with open(path, 'rb') as fh:
                    out[os.path.relpath(path, dest)] = fh.read()
            except OSError:
                out[os.path.relpath(path, dest)] = None
    return out


class SkillItem(SetupItem):
    """STORE/skills/llmpro/ equal to the skill that ships with llmpro. A copy that still has the marker line
    is llmpro's and is updated; a copy without it was edited by hand and is left alone."""
    name = 'skill: llmpro'

    def dest(self, ctx):
        return os.path.join(ctx.store, 'skills', SKILL_NAME)

    def survey(self, ctx):
        from ..setup import Item, tilde
        source = packaged_skill()
        if source is None:
            return Item(self.name, False, 'not shipped', ['this llmpro has no skill folder'])
        dest = self.dest(ctx)
        shown = tilde(dest)
        if not os.path.isdir(dest):
            return Item(self.name, True, 'missing', [], [f'write {shown} (the llmpro skill, for every assistant)'])
        if held_files(dest) == wanted_files(source):
            return Item(self.name, False, 'in place', [shown])
        try:
            with open(os.path.join(dest, 'SKILL.md'), encoding='utf-8') as fh:
                ours = MARKER in fh.read()
        except (OSError, UnicodeDecodeError):
            ours = False
        if ours:
            return Item(self.name, True, 'out of date', [], [f'update {shown} (a copy of the earlier files is kept)'])
        return Item(self.name, False, 'in place', ['edited by hand; llmpro setup leaves it'])

    def apply(self, ctx, undo):
        from ..setup import tilde
        from .. import register
        source = packaged_skill()
        dest = self.dest(ctx)
        if os.path.isdir(dest):
            backup = os.path.join(register.make_backup_dir(), 'skill-llmpro')
            shutil.copytree(dest, backup)
            shutil.rmtree(dest)
            undo.run('cp', '-rp', backup, dest)  # the script runs backwards: rm first, then cp
            undo.run('rm', '-rf', dest)
        else:
            undo.run('rm', '-rf', dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        for rel, data in wanted_files(source).items():
            path = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as fh:
                fh.write(data)
        print(f'wrote     {tilde(dest)}', file=sys.stderr)


class Memory(Plugin):
    name = 'memory'

    def capabilities(self, ctx):
        return ['memory.sync', 'memory.find', 'memory.summary']

    def setup_items(self, ctx):
        from .. import register
        return [*register.items(ctx), SkillItem()]


plugin = Memory()
