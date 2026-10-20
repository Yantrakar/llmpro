"""Convert AI-assistant conversations on this machine into STORE/history/<machine>/<tool>/...

Read-only on the sources. Re-runs update changed conversations in place and keep hand-written summaries.
"""
import bisect
import os
import subprocess
import sys

from .. import tools as outside
from ..redact import (MACHINE, load_manifest, manifest_entry, output_path, read_frontmatter,
                      render, save_manifest, step, sweep, write_text)
from ..sources import claude_code, codex, copilot, copilot_cli, opencode

TOOLS = {'codex': codex, 'claude-code': claude_code, 'copilot': copilot, 'copilot-cli': copilot_cli, 'opencode': opencode}


def store_commits(store):
    """-> [(committer time, 12-char id)] of the store's first-parent commits, oldest first, from one git call.
    Empty when there is no git, no commit or an error: the import then goes on without a harness."""
    git = outside.which('git')
    if not git:
        return []
    try:
        res = subprocess.run([git, '-C', store, 'log', '--first-parent', '--format=%H %ct', 'HEAD'],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if res.returncode != 0:
        return []
    rows = []
    for line in reversed(res.stdout.splitlines()):  # git lists newest first
        sha, _, when = line.partition(' ')
        if sha and when.strip().isdigit():
            rows.append((int(when), sha[:12]))
    rows.sort(key=lambda r: r[0])  # stable, so equal times keep the older commit first
    return rows


def harness_at(commits, started):
    """-> the id of the last commit at or before `started`, or ''."""
    if not commits or started is None:
        return ''
    i = bisect.bisect_right([c[0] for c in commits], started.timestamp())
    return commits[i - 1][1] if i else ''


def run(store, tools=None, only=None):
    """tools: the tool names to read (default: all); only: TOOL:ORIGINAL_ID to convert (no manifest update)."""
    only = set(only or [])
    manifest = {} if only else load_manifest(store)
    stats = {}
    commits = store_commits(store)  # one git call for the whole run
    pending = []  # rendered conversations; written after every password in this run has been learned

    for tool in tools or TOOLS:
        mod = TOOLS[tool]
        titles = mod.load_titles() if hasattr(mod, 'load_titles') else {}
        convs = {}
        for src in mod.source_files():
            try:
                conv = mod.parse(src, titles)
            except Exception as exc:  # keep going; record the failure
                key = (tool, MACHINE, 'conversation', src)
                manifest[key] = manifest_entry(tool, 'conversation', src, src, '', f'skipped:parse-error:{type(exc).__name__}')
                print(f'parse error {src}: {exc}', file=sys.stderr)
                continue
            if only and f'{tool}:{conv.original_id}' not in only:
                continue
            prev = convs.get(conv.original_id)
            if prev is not None:
                # same session stored twice (e.g. Copilot .json and .jsonl): keep the fuller one
                loser = conv if len(conv.items) < len(prev.items) else prev
                if loser is prev:
                    convs[conv.original_id] = conv
                key = (tool, MACHINE, 'conversation', loser.original_id + '@' + loser.source_file)
                manifest[key] = manifest_entry(tool, 'conversation', loser.source_file, loser.original_id, '', 'skipped:duplicate')
                continue
            convs[conv.original_id] = conv

        for conv in convs.values():
            key = (tool, MACHINE, 'conversation', conv.original_id)
            if conv.skip:
                manifest[key] = manifest_entry(tool, 'conversation', conv.source_file, conv.original_id, '', f'skipped:{conv.skip}')
                stats[(tool, 'skipped:' + conv.skip)] = stats.get((tool, 'skipped:' + conv.skip), 0) + 1
                continue
            out = output_path(store, conv)
            rel = os.path.relpath(out, store)
            old_rel = (manifest.get(key) or {}).get('output')
            summary = harness = ''
            for candidate in [os.path.join(store, old_rel)] if old_rel else []:
                fields = read_frontmatter(candidate)[0]
                summary, harness = fields.get('summary', ''), fields.get('harness', '')
            if os.path.exists(out):
                fields = read_frontmatter(out)[0]
                summary = summary or fields.get('summary', '')
                harness = harness or fields.get('harness', '')
            if summary.startswith('TODO'):
                summary = ''
            text, hits = render(conv, summary, str(harness or '') or harness_at(commits, conv.started))
            pending.append((tool, conv, key, out, rel, old_rel, text, hits))
        step(f'history: {tool}: {len(convs)} sessions read and redacted')

    step(f'history: sweeping and writing {len(pending)} conversations')
    for tool, conv, key, out, rel, old_rel, text, hits in pending:
        text = sweep(text, hits)  # every copy of every secret learned in this run, in any conversation
        if not os.path.exists(out) or open(out, encoding='utf-8').read() != text:
            write_text(out, text)
        if old_rel and old_rel != rel:
            old_abs = os.path.join(store, old_rel)
            if os.path.commonpath([old_abs, store]) == store and os.path.exists(old_abs):
                os.remove(old_abs)  # our own earlier output; the title changed
        manifest[key] = manifest_entry(tool, 'conversation', conv.source_file, conv.original_id, rel, 'imported')
        label = 'imported' + (' (redactions)' if hits else '')
        stats[(tool, label)] = stats.get((tool, label), 0) + 1
        if only:
            print(out)

    if not only:
        save_manifest(store, manifest)
    for (tool, label), n in sorted(stats.items()):
        print(f'{tool:12} {label:28} {n}', file=sys.stderr)
