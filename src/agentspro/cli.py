"""The `agentspro` command. Exit codes: 0 done, 1 a check or step failed, 2 usage or other error, 130
interrupted. Results go to stdout, progress to stderr."""
import sys


def build_parser():
    import argparse
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--store', metavar='PATH', default=argparse.SUPPRESS,
                        help='the store folder (default: $AGENTS_STORE, then ~/.agents)')
    common.add_argument('-q', '--quiet', action='store_true', default=argparse.SUPPRESS, help='no progress lines')
    common.add_argument('-v', '--verbose', action='store_true', default=argparse.SUPPRESS, help='more detail')
    p = argparse.ArgumentParser(prog='agentspro', parents=[common],
                                description='Keep AI assistant chats, memories and instructions in one git repo.')
    from . import __version__
    p.add_argument('--version', action='version', version=f'agentspro {__version__}')
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
    t.add_argument('--json', action='store_true', help='with -n: result as JSON')

    u = sub.add_parser('uninstall', parents=[common], help='take agentspro off this machine (the store is kept)')
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

    m = sub.add_parser('summary', parents=[common], help='set the summary at the top of a conversation')
    m.add_argument('conv', nargs='?', help='conversation id or file')
    m.add_argument('text', nargs='?', help='the summary')
    m.add_argument('--from', dest='from_file', metavar='FILE.jsonl', help='lines of {"id": ..., "summary": ...}')
    return p


def do_check(args, store):
    import contextlib
    import io
    import json
    from . import check
    buf = io.StringIO()
    capture = args.json or getattr(args, 'quiet', False)
    with contextlib.redirect_stdout(buf) if capture else contextlib.nullcontext():
        code = check.run(store, staged=args.staged, history=args.history, jobs=args.jobs, part=args.part,
                         context=args.context, no_cache=args.no_cache)
    text = buf.getvalue()
    if args.json:
        lines = text.splitlines()
        print(json.dumps({'passed': code == 0, 'exit': code, 'output': lines}))
    elif capture and code != 0:
        sys.stdout.write(text)
    return code


def run(argv):
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_usage(sys.stderr)
        return 2
    from . import store as cfg
    store = cfg.locate(getattr(args, 'store', None))
    if getattr(args, 'quiet', False):
        cfg.QUIET[0] = True
    if args.cmd in ('setup', 'uninstall'):
        from . import setup
        quiet = getattr(args, 'quiet', False)
        if args.cmd == 'setup':
            return setup.setup(store, dry_run=args.dry_run, yes=args.yes, hub=args.hub, as_json=args.json, quiet=quiet)
        return setup.uninstall(store, dry_run=args.dry_run, yes=args.yes, as_json=args.json, quiet=quiet)
    if args.cmd == 'sync':
        cfg.configure(store)
        from . import sync
        if args.dry_run:
            return sync.dry_run(store, as_json=args.json)
        return sync.run(store, local=args.local, quiet=getattr(args, 'quiet', False))
    if args.cmd == 'check':
        return do_check(args, store)
    if args.cmd == 'summary':
        if not args.from_file and not (args.conv and args.text):
            print('usage: agentspro summary CONV TEXT | agentspro summary --from FILE.jsonl', file=sys.stderr)
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


if __name__ == '__main__':
    sys.exit(main())
