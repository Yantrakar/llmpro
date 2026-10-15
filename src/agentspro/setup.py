"""`agentspro setup` and `agentspro uninstall`.

setup looks at four things and changes only what is missing:

  1. the store            clone it from --hub, or create it from the templates
  2. git settings         hooks (one line each), the manifest merge driver, merge.ours.driver
  3. session folders      each tool's chat-log folder moves into STORE/sessions/, a symlink stays behind
  4. instruction pointers each assistant's global instruction file reads the store's AGENTS.md

One preview, one y/N on a terminal. -y skips the question; without a terminal and without -y it refuses (exit 2).
-n prints the state of each item and changes nothing: exit 1 if one is missing, 0 if all are in place.
Every run that changes something writes an undo script to $XDG_STATE_HOME/agentspro and prints its path.

uninstall is the reverse, except that it never deletes the store.
Credential and settings files are never opened: folders are moved, instruction files are read and appended to.
"""
import datetime
import json
import os
import shlex
import shutil
import subprocess
import sys

from . import store as cfg
from .sync import HOOK_NAMES, MERGE_DRIVER, hook_text, hooks_dir

# tool, old path under HOME, path under STORE/sessions/
SESSION_FOLDERS = (
    ('claude-code', '.claude/projects', 'claude-code/projects'),
    ('claude-code', '.claude/file-history', 'claude-code/file-history'),
    ('codex', '.codex/sessions', 'codex/sessions'),
    ('codex', '.codex/archived_sessions', 'codex/archived_sessions'),
    ('copilot-cli', '.copilot/session-state', 'copilot-cli/session-state'),
    ('copilot', '.config/Code/User/workspaceStorage', 'copilot/workspaceStorage'),
    ('copilot', '.config/Code/User/globalStorage/emptyWindowChatSessions', 'copilot/emptyWindowChatSessions'),
    ('opencode', '.local/share/opencode', 'opencode/opencode'),
)

# tool, the tool's folder under HOME, its global instruction file in that folder, how it includes a file:
# 'at' = a line `@PATH` (Claude Code), 'text' = a plain sentence the assistant follows.
POINTER_FILES = (
    ('claude-code', '.claude', 'CLAUDE.md', 'at'),
    ('codex', '.codex', 'AGENTS.md', 'text'),
    ('copilot-cli', '.copilot', 'copilot-instructions.md', 'text'),
    ('opencode', '.config/opencode', 'AGENTS.md', 'text'),
)

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')


class SetupError(Exception):
    """Something stops the run: the message goes to stderr, the exit status is 2."""


# ---------------------------------------------------------------- small helpers

def tilde(path):
    h = cfg.home()
    return '~' + path[len(h):] if path == h or path.startswith(h + os.sep) else path


def under_home(rel):
    return os.path.join(cfg.home(), rel)


def git_out(store, *args):
    r = subprocess.run(['git', '-C', store, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def running_tools():
    """Names of assistants that are running now and write into the session folders. (The tests replace this.)"""
    try:
        ps = subprocess.run(['ps', '-eo', 'comm='], capture_output=True, text=True).stdout.split()
    except OSError:
        ps = []
    found = {n for n in ps if n in ('claude', 'codex', 'copilot', 'opencode', 'code')}
    try:
        if subprocess.run(['pgrep', '-f', '@github/copilot'], capture_output=True).returncode == 0:
            found.add('copilot-cli(node)')
    except OSError:
        pass
    return sorted(found)


def interactive():
    return sys.stdin.isatty() and sys.stderr.isatty()


def ask(question):
    try:
        return input(question + ' [y/N] ').strip().lower() in ('y', 'yes')
    except EOFError:
        return False


def state_dir():
    base = os.environ.get('XDG_STATE_HOME') or os.path.join(cfg.home(), '.local', 'state')
    return os.path.join(base, 'agentspro')


class Undo:
    """The shell commands that put things back; written in reverse order."""

    def __init__(self):
        self.lines = []

    def run(self, *cmd):
        self.lines.append(' '.join(shlex.quote(c) for c in cmd))

    def raw(self, line):
        self.lines.append(line)

    def note(self, text):
        self.lines.append('# ' + text)

    def write(self, kind):
        """-> the path of the script, or None if there is nothing to undo."""
        if not any(not line.startswith('#') for line in self.lines):
            return None
        d = state_dir()
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        path, n = os.path.join(d, f'{kind}-undo-{stamp}.sh'), 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(d, f'{kind}-undo-{stamp}-{n}.sh')
        body = ['#!/bin/sh', f'# Undo agentspro {kind} of {stamp}. Close the assistants first if it moves session folders.']
        body += list(reversed(self.lines))
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(body) + '\n')
        os.chmod(path, 0o700)
        return path


class Item:
    """One thing setup looks after: its state, what is missing, and how to put it right."""

    def __init__(self, name, missing, state, details=(), todo=()):
        self.name, self.missing, self.state = name, missing, state
        self.details, self.todo = list(details), list(todo)


# ---------------------------------------------------------------- 1. the store

def store_state(store):
    """-> 'present', 'absent', or 'foreign' (a non-empty folder that is not a store)."""
    if not os.path.exists(store):
        return 'absent'
    if os.path.isdir(os.path.join(store, '.git')) and os.path.isfile(os.path.join(store, 'config.toml')):
        return 'present'
    if os.path.isdir(store) and not os.listdir(store):
        return 'absent'
    return 'foreign'


def survey_store(store, hub):
    st = store_state(store)
    if st == 'present':
        return Item('store', False, 'present', [tilde(store)])
    if st == 'foreign':
        return Item('store', True, 'missing', [f'{tilde(store)} exists but is not a store (no .git or config.toml); move it away'],
                    [f'cannot use {tilde(store)}: it exists and is not a store'])
    if hub:
        return Item('store', True, 'missing', [f'no store at {tilde(store)}'], [f'clone {hub} to {tilde(store)}'])
    return Item('store', True, 'missing', [f'no store at {tilde(store)}'],
                [f'create a new store at {tilde(store)} from the templates (git init; AGENTS.md, FORMAT.md, config.toml, .gitignore, .gitattributes)'])


def apply_store(store, hub, undo):
    st = store_state(store)
    if st == 'foreign':
        raise SetupError(f'{store} exists and is not a store; move it away or use --store')
    if hub:
        os.makedirs(os.path.dirname(store), exist_ok=True)
        r = subprocess.run(['git', 'clone', '-o', 'hub', hub, store])  # may ask for the ssh password, once
        if r.returncode != 0:
            raise SetupError(f'git clone {hub} failed')
        if store_state(store) != 'present':
            raise SetupError(f'{hub} did not give a store (no config.toml)')
        undo.note(f'the store {store} was cloned from {hub}; it is kept (delete it by hand if you want it gone)')
        return
    os.makedirs(store, exist_ok=True)
    for name in sorted(os.listdir(TEMPLATES)):
        src = os.path.join(TEMPLATES, name)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(store, name))
    r = subprocess.run(['git', 'init', '-q', store])
    if r.returncode != 0:
        raise SetupError(f'git init {store} failed')
    undo.note(f'the store {store} was created; it is kept (delete it by hand if you want it gone)')


# ---------------------------------------------------------------- 2. git settings of the store

def check_toplevel(store):
    top = subprocess.run(['git', '-C', store, 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(store):
        raise SetupError(f'{store} is not the top of a git repository; not touching git settings there')


def config_get(store, key):
    r = subprocess.run(['git', '-C', store, 'config', '--local', '--get', key], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def hooks_path_is_old(value):
    return value is not None and value.rstrip('/').endswith('tools/hooks')


def git_settings_missing(store):
    """-> the names of the git settings that are not as setup wants them (empty: all in place)."""
    if store_state(store) != 'present':
        return ['store missing']
    check_toplevel(store)
    missing = []
    hd = hooks_dir(store)
    for name in HOOK_NAMES:
        try:
            have = open(os.path.join(hd, name), encoding='utf-8').read()
        except OSError:
            have = None
        if have != hook_text(name):
            missing.append(f'hook {name}')
    if config_get(store, 'merge.manifest.driver') != MERGE_DRIVER:
        missing.append('merge.manifest.driver')
    if config_get(store, 'merge.ours.driver') != 'true':
        missing.append('merge.ours.driver')
    if hooks_path_is_old(config_get(store, 'core.hooksPath')):
        missing.append('core.hooksPath (points at tools/hooks)')
    return missing


def survey_settings(store):
    if store_state(store) != 'present':
        return Item('git settings', True, 'missing', ['no store yet'],
                    ['write the hooks (.git/hooks: pre-commit, pre-merge-commit, pre-push), set the manifest merge driver and merge.ours.driver'])
    missing = git_settings_missing(store)
    if not missing:
        return Item('git settings', False, 'present', ['hooks, merge drivers'])
    return Item('git settings', True, 'missing', missing, ['set in the store: ' + ', '.join(missing)])


def apply_settings(store, undo):
    from .sync import repair_git_settings
    check_toplevel(store)
    hd = hooks_dir(store)
    for name in HOOK_NAMES:
        path = os.path.join(hd, name)
        try:
            old = open(path, encoding='utf-8').read()
            mode = os.stat(path).st_mode & 0o777
        except OSError:
            old, mode = None, 0o755
        if old == hook_text(name):
            continue
        if old is None:
            undo.run('rm', '-f', path)
        else:
            undo.raw(f'printf %s {shlex.quote(old)} > {shlex.quote(path)} && chmod {mode:o} {shlex.quote(path)}')
    for key in ('merge.manifest.driver', 'merge.ours.driver', 'core.hooksPath'):
        old = config_get(store, key)
        want = {'merge.manifest.driver': MERGE_DRIVER, 'merge.ours.driver': 'true'}.get(key)
        if old == want or (key == 'core.hooksPath' and not hooks_path_is_old(old)):
            continue
        if old is None:
            undo.run('git', '-C', store, 'config', '--local', '--unset', key)
        else:
            undo.run('git', '-C', store, 'config', '--local', key, old)
    repair_git_settings(store)


# ---------------------------------------------------------------- 3. session folders

def session_status(store, tool, old_rel, new_rel):
    """-> (state, src, dst). state: linked, foreign (a symlink to somewhere else), absent, todo, conflict."""
    src, dst = under_home(old_rel), os.path.join(store, 'sessions', new_rel)
    if os.path.islink(src):
        same = os.path.realpath(src) == os.path.realpath(dst)
        return ('linked' if same else 'foreign'), src, dst
    if not os.path.isdir(src):
        return 'absent', src, dst
    if os.path.lexists(dst):
        return 'conflict', src, dst
    return 'todo', src, dst


def survey_sessions(store):
    details, todo, missing = [], [], False
    for tool, old, new in SESSION_FOLDERS:
        st, src, dst = session_status(store, tool, old, new)
        text = {'linked': f'linked    {tilde(src)}',
                'foreign': f'skip      {tilde(src)} (a symlink elsewhere: {tilde(os.readlink(src)) if os.path.islink(src) else ""})',
                'absent': f'not here  {tilde(src)}',
                'todo': f'missing   {tilde(src)}',
                'conflict': f'skip      {tilde(src)} ({tilde(dst)} already exists; sort it out by hand)'}[st]
        details.append(f'{tool}: {text}')
        if st == 'todo':
            missing = True
            todo.append(f'move {tilde(src)} -> {tilde(dst)}, leave a symlink')
    return Item('session folders', missing, 'missing' if missing else 'present', details, todo)


def check_can_move(store):
    busy = running_tools()
    if busy:
        raise SetupError('close these first, then run again: ' + ' '.join(busy))
    if subprocess.run(['git', '-C', store, 'check-ignore', '-q', 'sessions/x/y'], capture_output=True).returncode != 0:
        raise SetupError(f'/sessions/ is not git-ignored in {store}; refusing to move raw logs into a store that would commit them')


def apply_sessions(store, undo):
    check_can_move(store)
    for tool, old, new in SESSION_FOLDERS:
        st, src, dst = session_status(store, tool, old, new)
        if st != 'todo':
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        os.symlink(dst, src)
        undo.raw(f'[ -L {shlex.quote(src)} ] && rm {shlex.quote(src)} && mv {shlex.quote(dst)} {shlex.quote(src)}')
        print(f'linked    {tilde(src)} -> {tilde(dst)}', file=sys.stderr)


# ---------------------------------------------------------------- 4. instruction pointers

def pointer_paths(store):
    """The store's AGENTS.md as ~/... (when under HOME) and as an absolute path."""
    path = os.path.join(store, 'AGENTS.md')
    return tilde(path), path


def pointer_line(kind, store):
    t = pointer_paths(store)[0]
    return f'@{t}' if kind == 'at' else f'Read {t} at the start of every session.'


def pointer_lines(store):
    """Every line setup could have written (the ~/ and the absolute form), to find them again."""
    out = set()
    for p in pointer_paths(store):
        out |= {f'@{p}', f'Read {p} at the start of every session.'}
    return out


def pointer_status(store, tool, folder, name, kind):
    """-> (state, path). state: present, todo (file missing or lacks the pointer), absent (tool not installed)."""
    d = under_home(folder)
    path = os.path.join(d, name)
    if not os.path.isdir(d):
        return 'absent', path
    try:
        text = open(path, encoding='utf-8').read()
    except FileNotFoundError:
        return 'todo', path
    except (OSError, UnicodeDecodeError):
        return 'unreadable', path
    return ('present' if any(p in text for p in pointer_paths(store)) else 'todo'), path


def survey_pointers(store):
    details, todo, missing = [], [], False
    for tool, folder, name, kind in POINTER_FILES:
        st, path = pointer_status(store, tool, folder, name, kind)
        label = {'present': 'present  ', 'todo': 'missing  ', 'absent': 'not here ', 'unreadable': 'skip     '}[st]
        details.append(f'{tool}: {label} {tilde(path)}' + (' (cannot read it)' if st == 'unreadable' else ''))
        if st == 'todo':
            missing = True
            todo.append(f'add `{pointer_line(kind, store)}` to {tilde(path)}' + ('' if os.path.exists(path) else ' (new file)'))
    return Item('instruction pointers', missing, 'missing' if missing else 'present', details, todo)


def apply_pointers(store, undo):
    for tool, folder, name, kind in POINTER_FILES:
        st, path = pointer_status(store, tool, folder, name, kind)
        if st != 'todo':
            continue
        line = pointer_line(kind, store) + '\n'
        if os.path.exists(path):
            size = os.path.getsize(path)
            with open(path, 'rb') as fh:
                fh.seek(max(size - 1, 0))
                tail = fh.read(1)
            lead = '\n' if size and tail != b'\n' else ''
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(lead + line)
            undo.run('truncate', '-s', str(size), path)
        else:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(line)
            undo.run('rm', '-f', path)
        print(f'pointer   {tilde(path)}', file=sys.stderr)


# ---------------------------------------------------------------- setup

def survey(store, hub):
    items = [survey_store(store, hub)]
    if items[0].todo and items[0].todo[0].startswith('cannot use'):
        return items
    items += [survey_settings(store), survey_sessions(store), survey_pointers(store)]
    return items


def render(items, with_todo=False):
    lines = []
    for it in items:
        lines.append(f'{it.name:<22}{it.state}')
        for d in (it.todo if with_todo else it.details):
            lines.append(f'    {d}')
    return lines


def consent(command, yes):
    """Ask once. -> True to go on. Raises SetupError when there is no terminal and no -y."""
    if yes:
        return True
    if not interactive():
        raise SetupError(f'agentspro {command} needs an answer and there is no terminal: '
                         f'use -y to apply, or -n to see what would change')
    return ask('Apply these changes?')


def finish(undo, kind):
    path = undo.write(kind)
    if path:
        print(f'To undo: sh {path}')
    return path


def setup(store, dry_run=False, yes=False, hub=None, as_json=False, quiet=False):
    """-> exit status: 0 done or all in place, 1 something is missing (-n) or the question was declined, 2 error."""
    try:
        items = survey(store, hub)
        missing = [i for i in items if i.missing]
        if dry_run:
            if as_json:
                print(json.dumps({'items': [{'name': i.name, 'present': not i.missing, 'details': i.details} for i in items],
                                  'missing': [i.name for i in missing]}))
            else:
                print('\n'.join(render(items)))
            return 1 if missing else 0
        if not missing:
            print('agentspro setup: everything is in place')
            return 0
        if items[0].todo and items[0].todo[0].startswith('cannot use'):
            raise SetupError(items[0].details[0])
        if hub and store_state(store) == 'present':
            print(f'agentspro setup: the store {tilde(store)} already exists; --hub is ignored', file=sys.stderr)
        print('agentspro setup would:', file=sys.stderr)
        for it in missing:
            for line in it.todo:
                print(f'  [{it.name}] {line}', file=sys.stderr)
        if any(i.name == 'session folders' for i in missing):
            busy = running_tools()
            if busy:
                raise SetupError('close these first, then run again: ' + ' '.join(busy))
        if not consent('setup', yes):
            print('Nothing changed.', file=sys.stderr)
            return 1
        undo = Undo()
        try:
            if items[0].missing:
                apply_store(store, hub, undo)
            if survey_settings(store).missing:
                apply_settings(store, undo)
            if survey_sessions(store).missing:
                apply_sessions(store, undo)
            if survey_pointers(store).missing:
                apply_pointers(store, undo)
        finally:
            finish(undo, 'setup')
        print('agentspro setup: done', file=sys.stderr)
        return 0
    except SetupError as exc:
        print(f'agentspro setup: {exc}', file=sys.stderr)
        return 2


# ---------------------------------------------------------------- uninstall

def plan_uninstall(store):
    """-> (sessions, hooks, configs, pointers): what uninstall would take away, from what is on disk now."""
    sessions = []
    for tool, old, new in SESSION_FOLDERS:
        st, src, dst = session_status(store, tool, old, new)
        if st == 'linked' and os.path.isdir(dst):
            sessions.append((src, dst))
    hooks, configs = [], []
    if store_state(store) == 'present':
        check_toplevel(store)
        hd = hooks_dir(store)
        for name in HOOK_NAMES:
            path = os.path.join(hd, name)
            try:
                if open(path, encoding='utf-8').read() == hook_text(name):
                    hooks.append(path)
            except OSError:
                pass
        for key, want in (('merge.manifest.driver', MERGE_DRIVER), ('merge.ours.driver', 'true')):
            if config_get(store, key) == want:
                configs.append((key, want))
    pointers = []
    wanted = pointer_lines(store)
    for tool, folder, name, kind in POINTER_FILES:
        path = os.path.join(under_home(folder), name)
        try:
            text = open(path, encoding='utf-8').read()
        except (OSError, UnicodeDecodeError):
            continue
        if any(line.strip() in wanted for line in text.splitlines()):
            pointers.append(path)
    return sessions, hooks, configs, pointers


def uninstall(store, dry_run=False, yes=False, as_json=False, quiet=False):
    """-> exit status, as setup(). The store itself is never deleted."""
    try:
        sessions, hooks, configs, pointers = plan_uninstall(store)
        todo = ([f'move {tilde(dst)} back to {tilde(src)} and remove the symlink' for src, dst in sessions]
                + [f'remove the hook {tilde(h)}' for h in hooks]
                + [f'unset git config {k} in the store' for k, _ in configs]
                + [f'remove the agentspro line from {tilde(p)}' for p in pointers])
        if dry_run:
            if as_json:
                print(json.dumps({'todo': todo}))
            else:
                print('\n'.join(todo) if todo else 'nothing of agentspro is set up on this machine')
            return 1 if todo else 0
        if not todo:
            print('agentspro uninstall: nothing to remove')
            return 0
        print('agentspro uninstall would (the store is kept):', file=sys.stderr)
        for line in todo:
            print(f'  {line}', file=sys.stderr)
        if sessions:
            busy = running_tools()
            if busy:
                raise SetupError('close these first, then run again: ' + ' '.join(busy))
        if not consent('uninstall', yes):
            print('Nothing changed.', file=sys.stderr)
            return 1
        undo = Undo()
        try:
            for src, dst in sessions:
                os.remove(src)
                shutil.move(dst, src)
                undo.raw(f'mv {shlex.quote(src)} {shlex.quote(dst)} && ln -s {shlex.quote(dst)} {shlex.quote(src)}')
            for path in hooks:
                old = open(path, encoding='utf-8').read()
                mode = os.stat(path).st_mode & 0o777
                os.remove(path)
                undo.raw(f'printf %s {shlex.quote(old)} > {shlex.quote(path)} && chmod {mode:o} {shlex.quote(path)}')
            for key, value in configs:
                subprocess.run(['git', '-C', store, 'config', '--local', '--unset', key], check=True)
                undo.run('git', '-C', store, 'config', '--local', key, value)
            if pointers:
                backup = os.path.join(state_dir(), 'backup-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
                os.makedirs(backup, mode=0o700, exist_ok=True)
                os.chmod(os.path.dirname(backup), 0o700)
                wanted = pointer_lines(store)
                for i, path in enumerate(pointers):
                    copy = os.path.join(backup, f'{i}-{os.path.basename(path)}')
                    shutil.copy2(path, copy)
                    text = open(path, encoding='utf-8').read()
                    kept = ''.join(l for l in text.splitlines(keepends=True) if l.strip() not in wanted)
                    if kept.strip():
                        with open(path, 'w', encoding='utf-8') as fh:
                            fh.write(kept)
                    else:
                        os.remove(path)  # only the pointer was in it; the backup keeps the file
                    undo.raw(f'cp -p {shlex.quote(copy)} {shlex.quote(path)}')
        finally:
            finish(undo, 'uninstall')
        print('agentspro uninstall: done; the store is kept', file=sys.stderr)
        return 0
    except SetupError as exc:
        print(f'agentspro uninstall: {exc}', file=sys.stderr)
        return 2
