"""`llmpro setup` and `llmpro uninstall`.

setup looks at five things and changes only what is missing:

  1. the store            clone it from --hub, or create it from the templates
  2. git settings         hooks (one line each), the manifest merge driver, merge.ours.driver
  3. session folders      each tool's chat-log folder moves into STORE/sessions/, a symlink stays behind
  4. instruction pointers each assistant's global instruction file reads the store's AGENTS.md
  5. harness              the parts named in [harness]: creates missing part folders, links each into the tools
                        that load it, puts scripts on PATH

One preview, one y/N on a terminal. -y skips the question; without a terminal and without -y it refuses (exit 2).
-n prints the state of each item and changes nothing: exit 1 if one is missing, 0 if all are in place.
Every run that changes something writes an undo script to $XDG_STATE_HOME/llmpro and prints its path.

uninstall is the reverse, except that it never deletes the store.
Credential and settings files are never opened: folders are moved, instruction files are read and appended to.
"""
import datetime
import os
import shlex
import shutil
import subprocess
import sys

from . import jsonout, store as cfg
from .sync import HOOK_NAMES, MERGE_DRIVER, OLD_MERGE_DRIVER, hook_is_ours, hook_text, hooks_dir

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
    ('opencode', '.local/state/opencode', 'opencode/state'),  # typed prompt history, last model
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


OLD_STATE_DIR_NAME = 'agentspro'


def state_dirs():
    """-> [the state folder, the one this tool used under its old name]; look in both for earlier undo scripts."""
    base = os.environ.get('XDG_STATE_HOME') or os.path.join(cfg.home(), '.local', 'state')
    return [os.path.join(base, 'llmpro'), os.path.join(base, OLD_STATE_DIR_NAME)]


def state_dir():
    return state_dirs()[0]


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
        body = ['#!/bin/sh', f'# Undo llmpro {kind} of {stamp}. Close the assistants first if it moves session folders.']
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
        self.plugin = None  # the name of the plugin that owns this item
        self.left = []  # what setup leaves for the person, each with its next step (a clash, a broken file)
        self.owner = None  # the plugins.base.SetupItem that applies it


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


def not_a_store(store):
    t = tilde(store)
    return (f'{t} exists and is not empty, but has no .git and config.toml, so it is not a store; llmpro changed '
            f'nothing there. Move it (mv {t} {t}.old) or choose another folder with --store PATH, then run llmpro setup')


def busy_message(busy, command):
    one = len(busy) == 1
    names = busy[0] if one else ', '.join(busy[:-1]) + ' and ' + busy[-1]
    return (f'{names} {"is" if one else "are"} running, and {command} would move {"its" if one else "their"} session '
            f'folders while {"it writes" if one else "they write"} to them. Nothing was changed. '
            f'Close {"it" if one else "them"}, then run llmpro {command} again')


def survey_store(store, hub):
    st = store_state(store)
    if st == 'present':
        return Item('store', False, 'present', [tilde(store)])
    if st == 'foreign':
        return Item('store', True, 'missing', [not_a_store(store)],
                    [f'cannot use {tilde(store)}: it exists and is not a store'])
    if hub:
        return Item('store', True, 'missing', [f'no store at {tilde(store)}'], [f'clone {hub} to {tilde(store)}'])
    return Item('store', True, 'missing', [f'no store at {tilde(store)}'],
                [f'create a new store at {tilde(store)} from the templates (git init; AGENTS.md, FORMAT.md, config.toml, .gitignore, .gitattributes)'])


def apply_store(store, hub, undo):
    st = store_state(store)
    if st == 'foreign':
        raise SetupError(not_a_store(store))
    if hub:
        os.makedirs(os.path.dirname(store), exist_ok=True)
        r = subprocess.run(['git', 'clone', '-o', 'hub', hub, store])  # may ask for the ssh password, once
        if r.returncode != 0:
            raise SetupError(f'git clone {hub} failed with exit status {r.returncode} (its message is above). Check the '
                             f'hub address and that you can reach it (git ls-remote {hub}), then run llmpro setup '
                             f'--hub {hub} again')
        if store_state(store) != 'present':
            t = tilde(store)
            raise SetupError(f'{hub} was cloned to {t}, but it has no config.toml, so it is not an llmpro store. The '
                             f'clone is left there. Check the hub address, move the clone away (mv {t} {t}.old), then '
                             f'run llmpro setup --hub HUB again')
        undo.note(f'the store {store} was cloned from {hub}; it is kept (delete it by hand if you want it gone)')
        return
    os.makedirs(store, exist_ok=True)
    for name in sorted(os.listdir(TEMPLATES)):
        src = os.path.join(TEMPLATES, name)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(store, name))
    r = subprocess.run(['git', 'init', '-q', store])
    if r.returncode != 0:
        raise SetupError(f'git init {tilde(store)} failed with exit status {r.returncode} (its message is above); the '
                         f'template files are already in that folder. Check that you can write there, then run '
                         f'llmpro setup')
    undo.note(f'the store {store} was created; it is kept (delete it by hand if you want it gone)')


# ---------------------------------------------------------------- 2. git settings of the store

def check_toplevel(store):
    top = subprocess.run(['git', '-C', store, 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(store):
        t = tilde(store)
        raise SetupError(f'{t} is not the top of its own git repository, so llmpro does not change git settings there. '
                         f'Run git init {t} if it should be one, or point --store at the top folder of the store, '
                         f'then run llmpro setup')


def config_get(store, key):
    r = subprocess.run(['git', '-C', store, 'config', '--local', '--get', key], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def hooks_path_is_old(value):
    return value is not None and value.rstrip('/').endswith('tools/hooks')


OLD_HOOKS_PATH = 'core.hooksPath still points at tools/hooks'


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
        missing.append(OLD_HOOKS_PATH)
    return missing


def survey_settings(store):
    if store_state(store) != 'present':
        return Item('git settings', True, 'missing', ['no store yet'],
                    ['write the hooks (.git/hooks: pre-commit, pre-merge-commit, pre-push), set the manifest merge driver and merge.ours.driver'])
    missing = git_settings_missing(store)
    if not missing:
        return Item('git settings', False, 'present', ['hooks, merge drivers'])
    todo = []
    to_set = [m for m in missing if m != OLD_HOOKS_PATH]
    if to_set:
        todo.append('set in the store: ' + ', '.join(to_set))
    if OLD_HOOKS_PATH in missing:
        todo.append('unset core.hooksPath in the store (it points at the old tools/hooks)')
    return Item('git settings', True, 'missing', missing, todo)


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
    """-> (state, src, dst). state: linked, foreign (a symlink to somewhere else), absent, todo, conflict,
    nested (dst would land in a folder another tool's link points at)."""
    src, dst = under_home(old_rel), os.path.join(store, 'sessions', new_rel)
    if os.path.islink(src):
        same = os.path.realpath(src) == os.path.realpath(dst)
        return ('linked' if same else 'foreign'), src, dst
    if not os.path.isdir(src):
        return 'absent', src, dst
    if os.path.lexists(dst):
        return 'conflict', src, dst
    if linked_above(dst):
        return 'nested', src, dst
    return 'todo', src, dst


def linked_above(dst):
    """The tool folder whose link points at a folder holding dst, or None. A store set up by hand can link
    ~/.copilot/session-state to sessions/copilot, where VS Code's folders would then be moved (2026-10-18)."""
    parent = os.path.realpath(os.path.dirname(dst))
    for _, old, _ in SESSION_FOLDERS:
        src = under_home(old)
        if os.path.islink(src):
            target = os.path.realpath(src)
            if parent == target or parent.startswith(target + os.sep):
                return src
    return None


def survey_sessions(store):
    details, todo, missing = [], [], False
    for tool, old, new in SESSION_FOLDERS:
        st, src, dst = session_status(store, tool, old, new)
        text = {'linked': f'linked    {tilde(src)}',
                'foreign': f'skip      {tilde(src)} (a symlink elsewhere: {tilde(os.readlink(src)) if os.path.islink(src) else ""})',
                'absent': f'not here  {tilde(src)}',
                'todo': f'missing   {tilde(src)}',
                'conflict': f'skip      {tilde(src)} ({tilde(dst)} already exists; sort it out by hand)',
                'nested': f'skip      {tilde(src)} ({tilde(dst)} would be inside the folder {tilde(linked_above(dst) or "")} '
                          'links to; sort it out by hand)'}[st]
        details.append(f'{tool}: {text}')
        if st == 'todo':
            missing = True
            todo.append(f'move {tilde(src)} -> {tilde(dst)}, leave a symlink')
    return Item('session folders', missing, 'missing' if missing else 'present', details, todo)


def check_can_move(store):
    busy = running_tools()
    if busy:
        raise SetupError(busy_message(busy, 'setup'))
    if subprocess.run(['git', '-C', store, 'check-ignore', '-q', 'sessions/x/y'], capture_output=True).returncode != 0:
        gi = tilde(os.path.join(store, '.gitignore'))
        raise SetupError(f'{gi} does not ignore /sessions/, so the raw logs setup would move there could be committed; '
                         f'llmpro does not move them. Add the line /sessions/ to {gi}, then run llmpro setup')


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


# ---------------------------------------------------------------- 5. harness

READS_AGENTS_SKILLS = ('copilot-cli', 'codex', 'opencode')  # read ~/.agents/skills themselves


def harness_tool_states(store, part, path):
    """-> [(tool, state)] for one part. The placed parts (skills, subagents, hooks, guardrails) come from harness."""
    from . import harness
    out = []
    for tool, folder, name, kind in POINTER_FILES:
        if part in harness.PLACED_PARTS:
            out.append((tool, harness.state(store, part, tool)[0]))
            continue
        st = pointer_status(store, tool, folder, name, kind)[0]
        if st != 'unreadable':
            out.append((tool, {'present': 'placed', 'todo': 'missing', 'absent': 'not here'}[st]))
    return out


def harness_step(line):
    """Sort key for a harness todo line: part folders, links, plugin files, then the claude commands."""
    if line.startswith('create ') and line.endswith('with a README.md'):
        return 0
    if line.startswith('link '):
        return 1
    return 2 if line.startswith('create ') else 3


def survey_harness(store):
    from . import harness
    cfg.load_harness(store)
    details, notes, todo, missing = [], [], [], False
    with harness.cached():
        for part, path in cfg.harness_parts():
            shown = path if os.path.exists(os.path.join(store, path)) else f'{path} (not in the store)'
            if part in harness.PLACED_PARTS:
                states, n, t, m = harness.survey_part(store, part, path)
                notes += n
                todo += t
                missing = missing or m
            else:
                states = ', '.join(f'{t} {s}' for t, s in harness_tool_states(store, part, path))
            details.append(f'{part}: {shown}  {states}')
        n, t, m = harness.survey_files(store)
    notes += n
    todo += t
    missing = missing or m
    notes = list(dict.fromkeys(notes))
    todo = sorted(dict.fromkeys(todo), key=harness_step)  # the order harness.apply does them in
    item = Item('harness', missing, 'missing' if missing else 'present', details + notes, todo)
    item.left = [n.strip() for n in notes if ' disabled: ' not in n]  # a plugin turned off is the person's choice
    return item


# ---------------------------------------------------------------- setup

def parse_only(only):
    """'store,git' -> {'store', 'git'}; None -> None (everything). Raises SetupError for a name nobody owns."""
    if only is None:
        return None
    from . import plugins
    names = {n.strip() for n in only.split(',') if n.strip()}
    known = {'store'} | {p.name for p in plugins.load()}
    unknown = sorted(names - known)
    if unknown or not names:
        raise SetupError(f'--only: unknown name {", ".join(unknown) or "(empty)"}; choose from {", ".join(sorted(known))}')
    return names


def survey_plugins(store, only=None):
    """-> the items of the plugins (name order). A plugin that is not ok adds one item that informs."""
    from . import plugins
    from .plugins.base import Status
    ctx = plugins.context(store)
    items = []
    for p in plugins.load():
        if only is not None and p.name not in only:
            continue
        try:
            st = p.status(ctx)
        except Exception as exc:
            st = Status('error', f'{type(exc).__name__}: {exc}')
        if st.state != 'ok':
            it = Item(f'plugin {p.name}', False, st.reason or st.state, [st.hint] if st.hint else [])
            it.plugin = p.name
            items.append(it)
            continue
        for si in p.setup_items(ctx):
            it = si.survey(ctx)
            it.plugin, it.owner = p.name, si
            items.append(it)
    return items


def survey(store, hub, only=None):
    """-> the core items, then the plugins' items. `only` (a set from parse_only) limits both."""
    items = []
    if only is None or 'store' in only:
        items = [survey_store(store, hub)]
        if items[0].todo and items[0].todo[0].startswith('cannot use'):
            return items
        items += [survey_settings(store), survey_sessions(store), survey_pointers(store),
                   survey_harness(store)]
    return items + survey_plugins(store, only)


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
        raise SetupError(f'llmpro {command} needs an answer and there is no terminal: '
                         f'use -y to apply, or -n to see what would change')
    return ask('Apply these changes?')


def finish(undo, kind):
    path = undo.write(kind)
    if path:
        print(f'To undo: sh {path}')
    return path


def stopped(exc, path, command):
    """The message for a run that failed half way: what failed, what state it left, and what to do next."""
    what = str(exc).rstrip('.') + '.'
    told = f'then run llmpro {command}' in what
    if not path:
        if told:
            return f'{what}\n  llmpro had changed nothing before this step.'
        return (f'{what}\n  llmpro had changed nothing before this step. Fix the cause, then run llmpro {command} '
                f'again; it does only what is still missing.')
    nxt = (f'Next: do what the message says; llmpro {command} again does only what is still missing.' if told else
           f'Next: fix the cause (run the command yourself to see its whole message), then run llmpro {command} '
           f'again; it does only what is still missing.')
    return (f'{what}\n  It stopped there. The lines above show what it changed before that; the steps after it were '
            f'not done.\n  {nxt} To take back what this run changed instead: sh {path}')


def item_json(i):
    d = {'name': i.name, 'present': not i.missing, 'details': i.details}
    if i.plugin:
        d['plugin'] = i.plugin
    return d


def yours(items):
    """At the end of setup: what an item that is in place still leaves for the person to do by hand, such as a
    line for a file llmpro left because it could not edit it safely."""
    lines = [f'  [{i.name}] {line}' for i in items if not i.missing for line in i.todo]
    lines += [f'  [{i.name}] {line}' for i in items for line in i.left]
    if lines:
        print('For you to do by hand:', file=sys.stderr)
        print('\n'.join(lines), file=sys.stderr)


def setup(store, dry_run=False, yes=False, hub=None, as_json=False, quiet=False, only=None):
    """-> exit status: 0 done or all in place, 1 something is missing (-n) or the question was declined, 2 error."""
    try:
        only = parse_only(only)
        core = only is None or 'store' in only
        items = survey(store, hub, only)
        missing = [i for i in items if i.missing]
        if dry_run:
            if as_json:
                from . import plugins
                jsonout.emit('setup', {'items': [item_json(i) for i in items], 'missing': [i.name for i in missing],
                                       'plugins': plugins.describe(plugins.context(store))}, ok=not missing)
            else:
                print('\n'.join(render(items)))
            return 1 if missing else 0
        if not missing:
            if any(i.left for i in items):
                print('llmpro setup: everything setup can do is in place')
            else:
                print('llmpro setup: everything is in place')
            yours(items)
            return 0
        if core and items[0].todo and items[0].todo[0].startswith('cannot use'):
            raise SetupError(items[0].details[0])
        if hub and store_state(store) == 'present':
            print(f'llmpro setup: the store {tilde(store)} already exists; --hub is ignored', file=sys.stderr)
        print('llmpro setup would:', file=sys.stderr)
        for it in missing:
            for line in it.todo:
                print(f'  [{it.name}] {line}', file=sys.stderr)
        if any(i.name == 'session folders' for i in missing):
            busy = running_tools()
            if busy:
                raise SetupError(busy_message(busy, 'setup'))
        if not consent('setup', yes):
            print('Nothing changed.', file=sys.stderr)
            return 1
        undo, failed = Undo(), None
        try:
            if core:
                if items[0].missing:
                    apply_store(store, hub, undo)
                if survey_settings(store).missing:
                    apply_settings(store, undo)
                if survey_sessions(store).missing:
                    apply_sessions(store, undo)
                if survey_pointers(store).missing:
                    apply_pointers(store, undo)
                if survey_harness(store).missing:
                    from . import harness
                    harness.apply(store, undo)
            from . import plugins
            ctx = plugins.context(store)
            for it in missing:
                if it.owner is not None:
                    it.owner.apply(ctx, undo)
        except SetupError as exc:
            failed = exc
        finally:
            path = finish(undo, 'setup')
        if failed:
            raise SetupError(stopped(failed, path, 'setup'))
        print('llmpro setup: done', file=sys.stderr)
        yours(items)
        return 0
    except SetupError as exc:
        print(f'llmpro setup: {exc}', file=sys.stderr)
        return 2


# ---------------------------------------------------------------- uninstall

def plan_uninstall(store):
    """-> (sessions, hooks, configs, pointers, links): what uninstall would take away, from what is on disk now."""
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
                if hook_is_ours(name, open(path, encoding='utf-8').read()):
                    hooks.append(path)
            except OSError:
                pass
        for key, wants in (('merge.manifest.driver', (MERGE_DRIVER, OLD_MERGE_DRIVER)), ('merge.ours.driver', ('true',))):
            have = config_get(store, key)
            if have in wants:
                configs.append((key, have))
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
    from . import harness
    return sessions, hooks, configs, pointers, harness.links(store), harness.installed_plugins(store)


def uninstall(store, dry_run=False, yes=False, as_json=False, quiet=False):
    """-> exit status, as setup(). The store itself is never deleted."""
    try:
        from . import harness
        sessions, hooks, configs, pointers, links, (exe, plugin_ids, market) = plan_uninstall(store)
        todo = ([f'move {tilde(dst)} back to {tilde(src)} and remove the symlink' for src, dst in sessions]
                + [f'remove the hook {tilde(h)}' for h in hooks]
                + [f'unset git config {k} in the store' for k, _ in configs]
                + [f'remove the llmpro line from {tilde(p)}' for p in pointers]
                + [f'replace the link {tilde(p)} with a copy of {tilde(real)}' for p, real in links]
                + [f'claude plugin uninstall {pid} --scope user' for pid in plugin_ids]
                + ([f'claude plugin marketplace remove {harness.MARKET}'] if market else []))
        if plugin_ids or market:
            todo.append('keep the hooks in ' + tilde(os.path.join(harness.part_dir(store, 'hooks'), 'claude-code/hooks/hooks.json')))
        from . import plugins
        ctx = plugins.context(store)
        owned = []  # (plugin name, SetupItem), plugins in reverse name order
        for p in reversed(plugins.load()):
            if p.status(ctx).state != 'ok':
                continue
            for si in p.setup_items(ctx):
                owned.append((p.name, si))
                todo += [f'[{p.name}] {line}' for line in si.plan_remove(ctx)]
        if dry_run:
            if as_json:
                jsonout.emit('uninstall', {'todo': todo}, ok=not todo)
            else:
                print('\n'.join(todo) if todo else 'nothing of llmpro is set up on this machine')
            return 1 if todo else 0
        if not todo:
            print('llmpro uninstall: nothing to remove')
            return 0
        print('llmpro uninstall would (the store is kept):', file=sys.stderr)
        for line in todo:
            print(f'  {line}', file=sys.stderr)
        if sessions:
            busy = running_tools()
            if busy:
                raise SetupError(busy_message(busy, 'uninstall'))
        if not consent('uninstall', yes):
            print('Nothing changed.', file=sys.stderr)
            return 1
        undo, failed = Undo(), None
        try:
            for name, si in owned:
                for line in si.remove(ctx, undo):
                    print(f'[{name}] {line}', file=sys.stderr)
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
            from . import harness
            for place, _ in links:
                harness.replace_with_copy(place, undo)
            if plugin_ids or market:
                harness.remove_plugins(store, exe, plugin_ids, market, undo)
        except SetupError as exc:
            failed = exc
        finally:
            path = finish(undo, 'uninstall')
        if failed:
            raise SetupError(stopped(failed, path, 'uninstall'))
        print('llmpro uninstall: done; the store is kept', file=sys.stderr)
        return 0
    except SetupError as exc:
        print(f'llmpro uninstall: {exc}', file=sys.stderr)
        return 2
