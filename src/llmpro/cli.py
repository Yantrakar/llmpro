"""The `llmpro` command. Exit codes: 0 done, 1 a check or step failed, 2 usage or other error, 130
interrupted. Results go to stdout, progress to stderr."""
import sys

# What this build can do, sorted. `llmpro --version --json` lists it; later packages add to it.
CAPABILITIES = ('check', 'check.json', 'find', 'find.json', 'mcp', 'setup', 'setup.dry-run', 'summary', 'sync',
                'sync.dry-run', 'uninstall', 'version.json')

RENAME_NOTE = 'agentspro is now llmpro; this name keeps working for now'

PLUGIN_COMMANDS = {}  # command name -> handler(args, store), filled by build_parser from the plugins


def build_parser():
    import argparse
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--store', metavar='PATH', default=argparse.SUPPRESS,
                        help='the store folder (default: $AGENTS_STORE, then ~/.agents)')
    common.add_argument('-q', '--quiet', action='store_true', default=argparse.SUPPRESS, help='no progress lines')
    common.add_argument('-v', '--verbose', action='store_true', default=argparse.SUPPRESS, help='more detail')
    p = argparse.ArgumentParser(prog='llmpro', parents=[common],
                                description='Keep AI assistant chats, memories and instructions in one git repo.')
    from . import __version__
    p.add_argument('--version', action='version', version=f'llmpro {__version__}')
    sub = p.add_subparsers(dest='cmd', metavar='COMMAND')

    s = sub.add_parser('sync', parents=[common], help='import, commit, and exchange commits with the hub')
    s.add_argument('--local', action='store_true', help='import and commit only; no fetch, merge or push')
    s.add_argument('-n', '--dry-run', action='store_true',
                   help='show what a sync would do (no login, no fetch); changes nothing')
    s.add_argument('--json', action='store_true', help='with -n: result as JSON')

    t = sub.add_parser('setup', parents=[common], help='set up this machine, or show what is missing')
    t.add_argument('-n', '--dry-run', action='store_true', help='show the state of each item; exit 1 if one is missing')
    t.add_argument('-y', '--yes', action='store_true', help='do not ask')
    t.add_argument('--hub', metavar='URL', help='clone the store from this hub when there is none')
    t.add_argument('--only', metavar='NAMES', help='comma-separated: store (the core items) and plugin names')
    t.add_argument('--json', action='store_true', help='with -n: result as JSON')

    u = sub.add_parser('uninstall', parents=[common], help='take llmpro off this machine (the store is kept)')
    u.add_argument('-n', '--dry-run', action='store_true', help='show what would be removed')
    u.add_argument('-y', '--yes', action='store_true', help='do not ask')
    u.add_argument('--json', action='store_true', help='with -n: result as JSON')

    c = sub.add_parser('check', parents=[common], help='secret check over the store')
    g = c.add_mutually_exclusive_group()
    g.add_argument('--staged', action='store_true', help='only what is staged')
    g.add_argument('--history', action='store_true', help='every file and every blob in history')
    c.add_argument('--json', action='store_true', help='result as JSON')
    c.add_argument('--context', action='store_true', help='show the text around each hit')
    c.add_argument('--no-cache', action='store_true', help='do not use the caches in .git/agentspro')
    c.add_argument('--jobs', type=int, default=None, help='worker processes')
    c.add_argument('--part', type=int, choices=(1, 2), default=None, help='run only part 1 or part 2')
    c.add_argument('--near-miss', action='store_true',
                   help='instead: list strings that look like secrets but were not flagged (a review aid; exit 0)')
    c.add_argument('--top', type=int, default=100, help='with --near-miss: groups to print (0 = all)')
    c.add_argument('--fix', action='store_true', help='rebuild memory/INDEX.md (nothing else is fixed)')

    f = sub.add_parser('find', parents=[common], help='search past chats and memories')
    f.add_argument('query', nargs='+', metavar='QUERY', help='words; all must occur in the same file')
    f.add_argument('--machine', metavar='M', help='only this machine (a memory: this scope)')
    f.add_argument('--tool', metavar='T', help='only this tool (leaves memories out)')
    f.add_argument('--since', metavar='YYYY-MM-DD', help='only on or after this date')
    g = f.add_mutually_exclusive_group()
    g.add_argument('--memory', action='store_true', help='memories only')
    g.add_argument('--history', action='store_true', help='conversations only')
    f.add_argument('--json', action='store_true', help='result as JSON')

    sub.add_parser('mcp', parents=[common], help='serve find, check and sync_status to assistants over stdio (MCP)')

    m = sub.add_parser('summary', parents=[common], help='set the summary at the top of a conversation')
    m.add_argument('conv', nargs='?', help='conversation id or file')
    m.add_argument('text', nargs='?', help='the summary')
    m.add_argument('--from', dest='from_file', metavar='FILE.jsonl', help='lines of {"id": ..., "summary": ...}')

    from . import plugins
    for plugin in plugins.load():
        for name, handler in plugin.add_commands(sub, common).items():
            PLUGIN_COMMANDS[name] = handler
    return p


def do_check(args, store):
    import contextlib
    import io
    from . import check, jsonout
    if args.near_miss:
        if args.staged or args.history or args.fix:
            print('--near-miss cannot be combined with --staged, --history or --fix', file=sys.stderr)
            return 2
        from . import nearmiss
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf) if args.json else contextlib.nullcontext():
            nearmiss.run(store, top=args.top, context=args.context, jobs=args.jobs)
        if args.json:
            jsonout.emit('check', {'passed': True, 'exit': 0, 'output': buf.getvalue().splitlines()})
        return 0
    buf = io.StringIO()
    capture = args.json or getattr(args, 'quiet', False)
    with contextlib.redirect_stdout(buf) if capture else contextlib.nullcontext():
        code = check.run(store, staged=args.staged, history=args.history, jobs=args.jobs, part=args.part,
                         context=args.context, no_cache=args.no_cache,
                         health=not (args.staged or args.history or args.part), fix=args.fix)
    text = buf.getvalue()
    if args.json:
        lines = text.splitlines()
        jsonout.emit('check', {'passed': code == 0, 'exit': code, 'output': lines}, ok=code == 0)
    elif capture and code != 0:
        sys.stdout.write(text)
    return code


def version_json():
    from . import jsonout, plugins, __version__
    ctx = plugins.context()
    caps = set(CAPABILITIES)
    for plugin in plugins.load():
        caps.update(plugin.capabilities(ctx))
    jsonout.emit('version', {'version': __version__, 'python': sys.version.split()[0],
                             'capabilities': sorted(caps), 'plugins': plugins.describe(ctx)})
    return 0


PASSTHROUGH_REST = []


def passthrough_split(argv):
    """argparse's REMAINDER fails when the first word after the command is an option (`llmpro git --help`).
    For a passthrough plugin command, cut argv after the command name; the rest goes to PASSTHROUGH_REST.
    (build_parser has filled PLUGIN_COMMANDS by now.)"""
    i = 0
    while i < len(argv):
        word = argv[i]
        if word == '--store':
            i += 2
        elif word in ('-q', '--quiet', '-v', '--verbose') or word.startswith('--store='):
            i += 1
        else:
            break
    if i < len(argv) and getattr(PLUGIN_COMMANDS.get(argv[i]), 'passthrough', False):
        PASSTHROUGH_REST[:] = argv[i + 1:]
        return argv[:i + 1]
    return argv


def run(argv):
    if sorted(argv) == ['--json', '--version']:  # argparse's --version exits before --json is read
        return version_json()
    parser = build_parser()
    args = parser.parse_args(passthrough_split(argv))
    if getattr(PLUGIN_COMMANDS.get(args.cmd), 'passthrough', False):
        args.args = list(PASSTHROUGH_REST)
    if not args.cmd:
        parser.print_usage(sys.stderr)
        return 2
    from . import store as cfg
    store = cfg.locate(getattr(args, 'store', None))
    if args.cmd in PLUGIN_COMMANDS:
        return PLUGIN_COMMANDS[args.cmd](args, store)
    if getattr(args, 'quiet', False):
        cfg.QUIET[0] = True
    if args.cmd in ('setup', 'uninstall'):
        from . import setup
        quiet = getattr(args, 'quiet', False)
        if args.cmd == 'setup':
            return setup.setup(store, dry_run=args.dry_run, yes=args.yes, hub=args.hub, as_json=args.json, quiet=quiet,
                               only=args.only)
        return setup.uninstall(store, dry_run=args.dry_run, yes=args.yes, as_json=args.json, quiet=quiet)
    if args.cmd == 'sync':
        cfg.configure(store)
        from . import sync
        if args.dry_run:
            return sync.dry_run(store, as_json=args.json)
        return sync.run(store, local=args.local, quiet=getattr(args, 'quiet', False))
    if args.cmd == 'check':
        return do_check(args, store)
    if args.cmd == 'find':
        from . import find
        history = not args.memory
        memory = not args.history
        return find.run(store, args.query, machine=args.machine, tool=args.tool, since=args.since,
                        history=history, memory=memory, as_json=args.json, verbose=getattr(args, 'verbose', False))
    if args.cmd == 'mcp':
        from . import mcp
        return mcp.serve(store)
    if args.cmd == 'summary':
        if not args.from_file and not (args.conv and args.text):
            print('usage: llmpro summary CONV TEXT | llmpro summary --from FILE.jsonl', file=sys.stderr)
            return 2
        cfg.configure(store, required=False)
        from . import summary
        return 1 if summary.run(store, args.conv, args.text, args.from_file) else 0
    return 2


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in ('hook', 'merge-driver'):
        from . import hot
        return hot.main(argv)
    try:
        return run(argv)
    except KeyboardInterrupt:
        return 130
    except SystemExit as exc:
        if exc.code is None or isinstance(exc.code, int):
            return exc.code or 0
        print(exc.code, file=sys.stderr)
        return 2


def alias_main(argv=None):
    """The `agentspro` command: the same as `llmpro`, with one line on stderr saying the name changed. Hooks
    and the merge driver run under the old name in stores set up before the rename, and stay silent."""
    import os
    argv = sys.argv[1:] if argv is None else list(argv)
    if not (argv and argv[0] in ('hook', 'merge-driver')) and os.environ.get('LLMPRO_NO_RENAME_NOTE') != '1':
        print(RENAME_NOTE, file=sys.stderr)
    return main(argv)


if __name__ == '__main__':
    sys.exit(main())
