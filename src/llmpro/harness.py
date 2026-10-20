"""Placing the harness: links from each tool's own folders into the store, and scripts on PATH.

A part (skills, subagents, hooks, guardrails, scripts) lives in a folder of the store. A tool loads it from a
place of its own under HOME. setup makes that place a symlink to the part's folder, after moving the files the
tool already has there into the store. Nothing here converts a format: the store keeps one folder per tool.

setup.py calls survey_part() for the survey, apply() to make the changes, and links() for uninstall.
"""
import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys

from . import setup as _setup, store as cfg, tools

# (part, tool, place, target). Checked 2026-10-20. `place` is the path under HOME the tool reads, or None.
# `target` is the path inside the part's folder that the place links to ('' is the folder itself), or
# 'unsupported:' and the reason. A target with a dot in its name is a file.
#
# Where each row comes from:
#   skills      claude-code  code.claude.com/docs/en/skills (personal skills in ~/.claude/skills)
#               codex        developers.openai.com/codex/skills and learn.chatgpt.com/docs/build-skills
#                            (user skills in ~/.agents/skills only)
#               copilot-cli  docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference
#                            (~/.copilot/skills, or ~/.agents/skills)
#               opencode     opencode.ai/docs/skills (~/.config/opencode/skills, or ~/.agents/skills)
#   subagents   claude-code  code.claude.com/docs/en/sub-agents (~/.claude/agents/*.md)
#               codex        developers.openai.com/codex/subagents (~/.codex/agents/*.toml)
#               copilot-cli  the cli-config-dir-reference above (~/.copilot/agents/*.agent.md)
#               opencode     opencode.ai/docs/agents (~/.config/opencode/agents/*.md)
#   hooks       claude-code  code.claude.com/docs/en/plugins/loading, /plugins/create-marketplace and
#                            /plugins/cli-reference: hooks come from a plugin. A local-path marketplace with
#                            relative plugin sources is loaded in place. `claude plugin marketplace list --json`
#                            and `claude plugin list --json` give JSON arrays; list entries have id, version,
#                            scope, enabled, installPath; marketplace entries have name, source, path.
#                            The target 'plugin:<name>' means the store's plugin of that name (see PLUGINS).
#               codex        learn.chatgpt.com/docs/hooks (~/.codex/hooks.json)
#               copilot-cli  docs.github.com/en/copilot/reference/hooks-reference and the
#                            cli-config-dir-reference (~/.copilot/hooks/*.json)
#               opencode     opencode.ai/docs/plugins (~/.config/opencode/plugins/, JS or TS)
#   guardrails  claude-code  a second plugin, like its hooks
#               the others   each loads hooks from one place only, so a guardrail goes in hooks/<tool>/
PLACES = (
    ('skills', 'claude-code', '.claude/skills', ''),
    ('skills', 'codex', None, 'unsupported: Codex reads only ~/.agents/skills'),
    ('skills', 'copilot-cli', '.copilot/skills', ''),
    ('skills', 'opencode', '.config/opencode/skills', ''),
    ('subagents', 'claude-code', '.claude/agents', 'claude-code'),
    ('subagents', 'codex', '.codex/agents', 'codex'),
    ('subagents', 'copilot-cli', '.copilot/agents', 'copilot-cli'),
    ('subagents', 'opencode', '.config/opencode/agents', 'opencode'),
    ('hooks', 'claude-code', None, 'plugin:harness-hooks'),
    ('hooks', 'codex', '.codex/hooks.json', 'codex/hooks.json'),
    ('hooks', 'copilot-cli', '.copilot/hooks', 'copilot-cli'),
    ('hooks', 'opencode', '.config/opencode/plugins', 'opencode'),
    ('guardrails', 'claude-code', None, 'plugin:harness-guardrails'),
    ('guardrails', 'codex', None, 'unsupported: each loads hooks from one place; put guardrails in hooks/<tool>/'),
    ('guardrails', 'copilot-cli', None, 'unsupported: each loads hooks from one place; put guardrails in hooks/<tool>/'),
    ('guardrails', 'opencode', None, 'unsupported: each loads hooks from one place; put guardrails in hooks/<tool>/'),
)

MARKET = 'llmpro-harness'
# part -> (plugin name, description). Each plugin lives in <part folder>/claude-code/.
PLUGINS = {
    'hooks': ('harness-hooks', 'Hooks from the llmpro store, loaded in place from hooks/claude-code.'),
    'guardrails': ('harness-guardrails', 'Guardrails from the llmpro store, loaded in place from guardrails/claude-code.'),
}
CLAUDE_TIMEOUT = 30
MARKET_FILE = '.claude-plugin/marketplace.json'

PLACED_PARTS = ('skills', 'subagents', 'hooks', 'guardrails', 'scripts')
NEVER_CREATED = ('instructions', 'memory')  # the store already has these
BIN = '.local/bin'

_WHERE = {
    'skills': ('Skills: one folder per skill, each with a SKILL.md. This is the only copy.',
               ['Claude Code: ~/.claude/skills links here.', 'Copilot CLI: ~/.copilot/skills links here, or it reads ~/.agents/skills.',
                'opencode: ~/.config/opencode/skills links here, or it reads ~/.agents/skills.',
                'Codex reads ~/.agents/skills only, so keep the store at ~/.agents for Codex.']),
    'subagents': ('Subagents: helper agents. The tools share no format, so each tool has its own folder: agents/<tool>/.',
                  ['Claude Code: ~/.claude/agents links to agents/claude-code/ (files ending .md).',
                   'Codex: ~/.codex/agents links to agents/codex/ (files ending .toml).',
                   'Copilot CLI: ~/.copilot/agents links to agents/copilot-cli/ (files ending .agent.md).',
                   'opencode: ~/.config/opencode/agents links to agents/opencode/ (files ending .md).']),
    'hooks': ('Hooks: commands a tool runs on its events. One folder per tool: hooks/<tool>/.',
              ['Claude Code: hooks come from the harness-hooks plugin in hooks/claude-code/, which setup installs through the llmpro-harness plugins marketplace.',
               'Codex: ~/.codex/hooks.json links to hooks/codex/hooks.json.',
               'Copilot CLI: ~/.copilot/hooks links to hooks/copilot-cli/ (files ending .json).',
               'opencode: ~/.config/opencode/plugins links to hooks/opencode/ (JS or TS files).']),
    'guardrails': ('Guardrails: hooks that stop an assistant. Today only Claude Code has its own place: guardrails/claude-code/.',
                   ['Claude Code: placed through the harness-guardrails plugin in guardrails/claude-code/, which setup installs through the llmpro-harness plugins marketplace.',
                    'Codex, Copilot CLI and opencode load hooks from one place, so put their guardrails in hooks/<tool>/.']),
    'scripts': ('Scripts: commands for every assistant and for you, as files at the top of this folder.',
                ['setup links each executable file into ~/.local/bin, so it is on PATH.',
                 'A file that is not executable is listed; run chmod +x on it, then run llmpro setup.',
                 'A name already in ~/.local/bin that is not our link is left alone and reported.']),
}


def readme_text(part):
    what, rows = _WHERE[part]
    return what + '\n\nWhere each tool gets it:\n\n' + ''.join(f'- {r}\n' for r in rows) + \
        '\nllmpro setup -n shows the state of each link. Do not store secrets here.\n'


# ---------------------------------------------------------------- the table

def part_dir(store, part):
    path = cfg.HARNESS.get(part) or cfg.HARNESS_DEFAULTS[part]
    return os.path.join(store, path.rstrip('/'))


def rows(part=None):
    return [r for r in PLACES if part is None or r[0] == part]


def _row(part, tool):
    for r in PLACES:
        if r[0] == part and r[1] == tool:
            return r
    return None


def _tool_folder(tool):
    for t, folder, _, _ in _setup.POINTER_FILES:
        if t == tool:
            return _setup.under_home(folder)
    return None


def _is_file(target):
    return '.' in os.path.basename(target)


def _same(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def _target_path(store, part, target):
    return os.path.join(part_dir(store, part), target) if target else part_dir(store, part)


def _names(path):
    try:
        return set(os.listdir(path)) if os.path.isdir(path) else set()
    except OSError:
        return set()


def state(store, part, tool):
    """-> (state, detail) of one part for one tool. See the module docstring."""
    row = _row(part, tool)
    if row is None:
        return 'unsupported', 'llmpro does not place this part for this tool'
    _, _, place, target = row
    if part == 'skills' and tool in _setup.READS_AGENTS_SKILLS and _same(store, _setup.under_home('.agents')):
        return 'placed', ''
    if target.startswith('plugin:'):
        return _plugin_state(store, part)
    if place is None or target.startswith('unsupported:'):
        return 'unsupported', target[len('unsupported:'):].strip()
    folder = _tool_folder(tool)
    if folder is None or not os.path.isdir(folder):
        return 'not here', ''
    p, t = _setup.under_home(place), _target_path(store, part, target)
    if os.path.islink(p):
        if _same(p, t):
            return 'placed', ''
        where = os.path.realpath(p)
        return 'linked elsewhere', f'{_setup.tilde(p)} points at {_setup.tilde(where)}; llmpro leaves it; remove the link to let setup place it'
    if os.path.lexists(p):
        if _is_file(target):
            if not os.path.isfile(p):
                return 'clash', f'{_setup.tilde(p)} is a folder, not a file; move it away, then run llmpro setup'
            if os.path.lexists(t):
                return 'clash', f'merge {_setup.tilde(p)} into {_setup.tilde(t)} by hand, then run llmpro setup'
            return 'missing', ''
        if not os.path.isdir(p):
            return 'clash', f'{_setup.tilde(p)} is a file, not a folder; move it away, then run llmpro setup'
        both = sorted(_names(p) & _names(t))
        if both:
            return 'clash', '; '.join(f'merge {_setup.tilde(p)}/{n} into {_setup.tilde(t)}/{n} by hand, then run llmpro setup' for n in both)
        return 'missing', ''
    if _is_file(target) and not os.path.lexists(t):
        return 'nothing yet', ''
    return 'missing', ''


# ---------------------------------------------------------------- scripts

def script_states(store):
    """-> [(name, state, detail)] for each regular file at the top of the scripts folder, README.md apart."""
    d = part_dir(store, 'scripts')
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        src = os.path.join(d, name)
        if name == 'README.md' or not os.path.isfile(src):
            continue
        dest = _setup.under_home(os.path.join(BIN, name))
        if os.path.islink(dest) and _same(dest, src):
            out.append((name, 'placed', ''))
        elif os.path.lexists(dest):
            out.append((name, 'clash', f'{_setup.tilde(dest)} is taken by something else; move it away or rename the script, then run llmpro setup'))
        elif not os.access(src, os.X_OK):
            out.append((name, 'not executable', f'chmod +x {_setup.tilde(src)}, then run llmpro setup'))
        else:
            out.append((name, 'missing', ''))
    return out



# ---------------------------------------------------------------- Claude Code plugins

_cache = None


@contextlib.contextmanager
def cached():
    """Within this block each `claude` listing runs at most once."""
    global _cache
    old, _cache = _cache, {}
    try:
        yield
    finally:
        _cache = old


def _plugin_dir(store, part):
    return os.path.join(part_dir(store, part), 'claude-code')


def _rel(store, path):
    return os.path.relpath(path, store)


def _market_json(store):
    return {'name': MARKET, 'owner': {'name': 'llmpro'},
            'plugins': [{'name': PLUGINS[part][0], 'source': './' + _rel(store, _plugin_dir(store, part))}
                        for part in PLUGINS]}


def plugin_files(store):
    """-> [(path, text)] of the plugin files the store should have, in the order they are made."""
    out = [(os.path.join(store, MARKET_FILE), json.dumps(_market_json(store), indent=2) + '\n')]
    for part, (name, desc) in PLUGINS.items():
        base = _plugin_dir(store, part)
        out.append((os.path.join(base, '.claude-plugin', 'plugin.json'),
                    json.dumps({'name': name, 'description': desc}, indent=2) + '\n'))
        out.append((os.path.join(base, 'hooks', 'hooks.json'), json.dumps({'hooks': {}}, indent=2) + '\n'))
    return out


def _market_problem(store):
    """-> None, or what is wrong with an existing marketplace.json."""
    path = os.path.join(store, MARKET_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return f'{MARKET_FILE} does not parse ({exc}); llmpro leaves it; fix or remove it, then run llmpro setup'
    plugins = data.get('plugins') if isinstance(data, dict) else None
    have = {p.get('name') for p in plugins if isinstance(p, dict)} if isinstance(plugins, list) else set()
    lacks = [PLUGINS[part][0] for part in PLUGINS if PLUGINS[part][0] not in have]
    if lacks:
        return f'{MARKET_FILE} lacks the plugin {" and ".join(lacks)}; llmpro leaves it; add it or remove the file, then run llmpro setup'
    return None


def survey_files(store):
    """-> ([notes], [todo lines], missing) for the plugin files."""
    notes, todo = [], []
    problem = _market_problem(store)
    if problem:
        notes.append('  ' + problem)
    for path, _ in plugin_files(store):
        if not os.path.exists(path):
            todo.append(f'create {_rel(store, path)}')
    return notes, todo, bool(todo)


def _short(text):
    lines = (text or '').strip().splitlines()
    return lines[0] if lines else 'no message'


def _claude_list(exe, args):
    """-> (list, None) or (None, what went wrong), for `claude <args>`."""
    shown = 'claude ' + ' '.join(args)
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f'`{shown}` did not finish in {CLAUDE_TIMEOUT} s'
    except OSError as exc:
        return None, f'`{shown}` could not run ({exc.strerror or exc})'
    if r.returncode != 0:
        return None, f'`{shown}` exited with status {r.returncode}: {_short(r.stderr)}'
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return None, f'`{shown}` exited with status 0 but printed no JSON: {_short(r.stderr or r.stdout)}'
    if not isinstance(data, list) or not all(isinstance(e, dict) for e in data):
        return None, f'`{shown}` exited with status 0 but did not print a JSON list: {_short(r.stderr)}'
    return data, None


def _probe(store):
    exe = tools.which('claude')
    if not exe or not os.path.isdir(_setup.under_home('.claude')):
        return {'state': 'not here'}
    markets, bad = _claude_list(exe, ['plugin', 'marketplace', 'list', '--json'])
    if bad is None:
        plugins, bad = _claude_list(exe, ['plugin', 'list', '--json'])
    if bad:
        return {'state': 'unknown', 'detail': bad}
    here, elsewhere = False, None
    for m in markets:
        if m.get('name') == MARKET:
            # a directory marketplace names its folder as path or installLocation, depending on the version
            where = [m[k] for k in ('path', 'installLocation') if isinstance(m.get(k), str)]
            if any(_same(w, store) for w in where):
                here = True
            else:
                elsewhere = (where or [None])[0] or str(m.get('source') or 'somewhere else')
    return {'state': 'ok', 'exe': exe, 'here': here, 'elsewhere': elsewhere,
            'plugins': {e.get('id'): bool(e.get('enabled')) for e in plugins}}


def _info(store):
    if _cache is None:
        return _probe(store)
    key = os.path.realpath(store)
    if key not in _cache:
        _cache[key] = _probe(store)
    return _cache[key]


def _plugin_state(store, part, info=None):
    info = info or _info(store)
    if info['state'] != 'ok':
        return info['state'], info.get('detail', '')
    problem = _market_problem(store)
    if problem:
        return 'unknown', problem
    if info['elsewhere']:
        return 'linked elsewhere', (f'the marketplace {MARKET} points at {info["elsewhere"]}; llmpro leaves it; '
                                    f'run claude plugin marketplace remove {MARKET} to let setup place it')
    pid = f'{PLUGINS[part][0]}@{MARKET}'
    if info['here'] and pid in info['plugins']:
        if info['plugins'][pid]:
            return 'placed', ''
        return 'disabled', f'you turned it off; run claude plugin enable {pid} to turn it on'
    return 'missing', ''


def _plugin_todo(store, part):
    lines = []
    if not _info(store).get('here'):
        lines.append(f'claude plugin marketplace add {store}')
    lines.append(f'claude plugin install {PLUGINS[part][0]}@{MARKET} --scope user')
    return lines


def _claude(exe, args, done):
    shown = 'claude ' + ' '.join(args)
    after = (' Already done: ' + '; '.join(done) + '. The undo script covers it.') if done else ' Nothing was changed through claude yet.'
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise _setup.SetupError(f'`{shown}` did not finish in {CLAUDE_TIMEOUT} s.{after}')
    except OSError as exc:
        raise _setup.SetupError(f'`{shown}` could not run ({exc.strerror or exc}).{after}')
    if r.returncode != 0:
        raise _setup.SetupError(f'`{shown}` failed with exit status {r.returncode}: {r.stderr.strip() or "no message"}.{after}')
    _say('ran', shown)


def _make_plugin_files(store, undo):
    for path, text in plugin_files(store):
        if os.path.exists(path):
            continue
        _make_dir(os.path.dirname(path), undo)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        undo.raw(f'rm -f {shlex.quote(path)}')
        _say('created', _setup.tilde(path))


def _apply_plugins(store, undo):
    info = _probe(store)
    need = [part for part in PLUGINS if _plugin_state(store, part, info)[0] == 'missing']
    if not need:
        return
    exe, done = info['exe'], []
    if not info['here']:
        _claude(exe, ['plugin', 'marketplace', 'add', store], done)
        undo.run('claude', 'plugin', 'marketplace', 'remove', MARKET)
        done.append(f'added the marketplace {MARKET}')
    for part in need:
        pid = f'{PLUGINS[part][0]}@{MARKET}'
        _claude(exe, ['plugin', 'install', pid, '--scope', 'user'], done)
        undo.run('claude', 'plugin', 'uninstall', pid, '--scope', 'user')
        done.append(f'installed {pid}')


def installed_plugins(store):
    """-> (exe, [plugin ids installed from our marketplace], whether the marketplace is ours), for uninstall."""
    info = _probe(store)
    if info['state'] != 'ok' or info['elsewhere']:
        return None, [], False
    ids = [f'{PLUGINS[part][0]}@{MARKET}' for part in PLUGINS if f'{PLUGINS[part][0]}@{MARKET}' in info['plugins']]
    return info['exe'], ids, info['here']


def remove_plugins(store, exe, ids, market, undo):
    done = []
    for pid in ids:
        _claude(exe, ['plugin', 'uninstall', pid, '--scope', 'user'], done)
        undo.run('claude', 'plugin', 'install', pid, '--scope', 'user')
        done.append(f'uninstalled {pid}')
    if market:
        _claude(exe, ['plugin', 'marketplace', 'remove', MARKET], done)
        undo.run('claude', 'plugin', 'marketplace', 'add', store)


# ---------------------------------------------------------------- survey

def _folder_missing(store, part):
    return part not in NEVER_CREATED and not os.path.isdir(part_dir(store, part))


def survey_part(store, part, path):
    """-> (text for the part line, [notes], [todo lines], missing) for skills, subagents, hooks, guardrails, scripts."""
    notes, todo, missing = [], [], False
    if _folder_missing(store, part):
        missing = True
        todo.append(f'create {path if path.endswith("/") else path + "/"} with a README.md')
    if part == 'scripts':
        states = script_states(store)
        if not states:
            return 'none yet', notes, todo, missing
        bits = []
        for st in ('placed', 'missing', 'not executable', 'clash'):
            names = [n for n, s, _ in states if s == st]
            if names:
                bits.append(f'{len(names)} {st}' + ('' if st == 'placed' else f' ({", ".join(names)})'))
        for name, st, detail in states:
            if st in ('clash', 'not executable'):
                notes.append(f'  scripts {name} {st}: {detail}')
            if st == 'missing':
                missing = True
                todo.append(f'link {_setup.tilde(_setup.under_home(os.path.join(BIN, name)))} -> '
                            f'{_setup.tilde(os.path.join(part_dir(store, "scripts"), name))}')
        return ', '.join(bits), notes, todo, missing
    cells, unsupported = [], {}  # reason -> tools, shown once after the other cells
    for _, tool, place, target in rows(part):
        st, detail = state(store, part, tool)
        if st == 'unsupported':
            unsupported.setdefault(detail, []).append(tool)
        else:
            cells.append(f'{tool} {st}')
        if st in ('clash', 'linked elsewhere', 'unknown', 'disabled'):
            notes.append(f'  {tool} {part} {st}: {detail}')
        elif st == 'missing' and target.startswith('plugin:'):
            missing = True
            todo += _plugin_todo(store, part)
        elif st == 'missing':
            missing = True
            p, t = _setup.under_home(place), _target_path(store, part, target)
            more = ''
            if os.path.lexists(p) and not os.path.islink(p):
                n = len(os.listdir(p)) if os.path.isdir(p) else 1
                if n:
                    more = f' (moves its {n} file{"" if n == 1 else "s"} into the store first)' if os.path.isdir(p) \
                        else ' (moves its file into the store first)'
            todo.append(f'link {_setup.tilde(p)} -> {_setup.tilde(t)}{more}')
    cells += [f'{", ".join(tools_)} unsupported ({reason})' for reason, tools_ in unsupported.items()]
    return ', '.join(cells), notes, todo, missing


# ---------------------------------------------------------------- apply

def _say(word, text):
    print(f'{word:<10}{text}', file=sys.stderr)


def _make_part_folder(store, part, undo):
    d = part_dir(store, part)
    os.makedirs(d)
    with open(os.path.join(d, 'README.md'), 'w', encoding='utf-8') as fh:
        fh.write(readme_text(part))
    q = shlex.quote(d)
    undo.raw(f'[ "$(ls -A {q})" = README.md ] && rm {q}/README.md && rmdir {q}')
    _say('created', _setup.tilde(d))


def _make_dir(path, undo):
    """Make a folder and its missing parents; the undo removes each only if it is empty."""
    todo = []
    d = path
    while d and not os.path.isdir(d):
        todo.append(d)
        d = os.path.dirname(d)
    for d in reversed(todo):
        os.mkdir(d)
        undo.raw(f'rmdir {shlex.quote(d)} 2>/dev/null || true')


def _link_place(store, part, tool, undo):
    _, _, place, target = _row(part, tool)
    p, t = _setup.under_home(place), _target_path(store, part, target)
    q, qt = shlex.quote(p), shlex.quote(t)
    if _is_file(target):
        _make_dir(os.path.dirname(t), undo)
        if os.path.lexists(p):
            shutil.move(p, t)
            undo.raw(f'[ -L {q} ] && rm {q} && mv {qt} {q}')
        else:
            undo.raw(f'[ -L {q} ] && rm {q}')
    else:
        _make_dir(t, undo)
        if os.path.lexists(p):
            names = sorted(os.listdir(p))
            for n in names:
                shutil.move(os.path.join(p, n), os.path.join(t, n))
            os.rmdir(p)
            moves = ''.join(f' && mv {shlex.quote(os.path.join(t, n))} {shlex.quote(os.path.join(p, n))}' for n in names)
            undo.raw(f'[ -L {q} ] && rm {q} && mkdir {q}{moves}')
        else:
            undo.raw(f'[ -L {q} ] && rm {q}')
    os.symlink(t, p)
    _say('linked', f'{_setup.tilde(p)} -> {_setup.tilde(t)}')


def apply(store, undo):
    """Do what survey_part listed, in that order: part folders, links into the tools, scripts."""
    cfg.load_harness(store)
    for part in PLACED_PARTS:
        if _folder_missing(store, part):
            _make_part_folder(store, part, undo)
    for part, tool, place, target in PLACES:
        if not target.startswith('plugin:') and state(store, part, tool)[0] == 'missing':
            _link_place(store, part, tool, undo)
    for name, st, _ in script_states(store):
        if st != 'missing':
            continue
        dest, src = _setup.under_home(os.path.join(BIN, name)), os.path.join(part_dir(store, 'scripts'), name)
        _make_dir(os.path.dirname(dest), undo)
        os.symlink(src, dest)
        undo.raw(f'[ -L {shlex.quote(dest)} ] && rm {shlex.quote(dest)}')
        _say('linked', f'{_setup.tilde(dest)} -> {_setup.tilde(src)}')
    _make_plugin_files(store, undo)
    _apply_plugins(store, undo)


# ---------------------------------------------------------------- uninstall

def _into(store, path):
    rs = os.path.realpath(store)
    real = os.path.realpath(path)
    return os.path.exists(real) and (real == rs or real.startswith(rs + os.sep))


def links(store):
    """-> [(place, where it resolves)] for every place in PLACES and every ~/.local/bin entry that is a symlink
    resolving into the store."""
    cands = [_setup.under_home(place) for _, _, place, _ in PLACES if place]
    bindir = _setup.under_home(BIN)
    if os.path.isdir(bindir):
        cands += [os.path.join(bindir, n) for n in sorted(os.listdir(bindir))]
    out, seen = [], set()
    for p in cands:
        if p not in seen and os.path.islink(p) and _into(store, p):
            seen.add(p)
            out.append((p, os.path.realpath(p)))
    return out


def replace_with_copy(place, undo):
    """Replace a symlink with a copy of what it points at; the undo removes the copy and links it again."""
    orig, real = os.readlink(place), os.path.realpath(place)
    os.remove(place)
    if os.path.isdir(real):
        shutil.copytree(real, place, symlinks=True)
    else:
        shutil.copy2(real, place)
    q = shlex.quote(place)
    undo.raw(f'rm -rf {q} && ln -s {shlex.quote(orig)} {q}')
    _say('copied', f'{_setup.tilde(real)} to {_setup.tilde(place)}')
