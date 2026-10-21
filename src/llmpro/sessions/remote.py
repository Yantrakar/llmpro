"""Sessions on other machines, asked over plain ssh (DECISIONS S4, S5).

The remote command is built only from words quoted with shlex.quote. ssh never asks for a password
(BatchMode), every call puts `--` before the target, and a target that starts with `-` is refused. Rows that
come back are read with Row.from_dict; anything that does not parse is an error, never a row. Every string
that came from the other machine goes through `clean`.
"""
import dataclasses
import json
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor

from .. import tools
from . import FORMAT, ID, STATES, Row, clean

ARGS = ['resume', '--local', '--json', '--no-line']


def remote_string(m, args):
    """The command the remote shell runs: the llmpro path, then each argument, every word quoted."""
    path = m.llmpro
    for prefix in ('~/', '$HOME/'):
        if path.startswith(prefix):
            first = '"$HOME"/' + shlex.quote(path[len(prefix):])
            break
    else:
        first = shlex.quote(path)
    return ' '.join([first] + [shlex.quote(str(a)) for a in args])


def command(m, args, control=None):
    """-> the ssh argv for one machine. ValueError for a machine that must not be asked."""
    if m.error or not m.ssh or m.ssh.startswith('-'):
        raise ValueError(f'{m.name}: not a machine that can be asked')
    argv = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5']
    if control:
        argv += ['-o', f'ControlPath={control}']
    return argv + ['--', m.ssh, remote_string(m, args)]


def read_row(d, name):
    """A remote row as this side keeps it: parsed, checked, cleaned, with machine and line set here."""
    row = Row.from_dict(d)
    if not ID.fullmatch(row.id) or row.state not in STATES:
        raise ValueError('bad id or state')
    changes = {f.name: clean(getattr(row, f.name)) for f in dataclasses.fields(row)
               if isinstance(getattr(row, f.name), str)}
    changes.update(machine=name, line='')
    return dataclasses.replace(row, **changes)


def ask(m, timeout=10, control=None):
    """-> (rows, error): the open sessions on machine m, or [] and one line saying why not."""
    if m.error:
        return [], m.error
    ssh = tools.which('ssh')
    if not ssh:
        return [], f'{m.name}: ssh is not installed here'
    argv = command(m, ARGS, control)
    argv[0] = ssh
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors='replace', stdin=subprocess.DEVNULL,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return [], f'{m.name}: no answer within {timeout} seconds'
    except OSError as exc:
        return [], f'{m.name}: cannot run ssh ({type(exc).__name__})'
    err = r.stderr or ''
    if r.returncode == 255:
        if 'Permission denied' in err:
            return [], (f'{m.name}: ssh could not log in without a password; set up a key, or run '
                        f'ssh {m.ssh} once')
        return [], f'{m.name}: cannot reach {m.ssh}'
    if r.returncode != 0 and (r.returncode == 127 or 'not found' in err):
        return [], f'{m.name}: llmpro not found at {m.llmpro}; set llmpro in [machines]'
    if r.returncode == 2 and 'live sessions need Linux' in err:
        return [], f'{m.name}: live sessions need Linux'
    old = f'{m.name}: its llmpro does not know resume --json; run pipx upgrade llmpro there'
    try:
        body = json.loads(r.stdout)
    except ValueError:
        return [], old
    if not isinstance(body, dict) or body.get('format') != FORMAT or not isinstance(body.get('rows'), list):
        return [], old
    rows, dropped = [], 0
    for d in body['rows']:
        try:
            rows.append(read_row(d, m.name))
        except (ValueError, TypeError):
            dropped += 1
    return rows, (f'{m.name}: {dropped} of {len(body["rows"])} rows could not be read' if dropped else '')


def ask_all(machines, timeout=10, control=None):
    """-> [(machine, rows, error)] in the order given, all asked at once."""
    control = control or {}
    if not machines:
        return []
    with ThreadPoolExecutor(max_workers=len(machines)) as pool:
        futures = [pool.submit(ask, m, timeout, control.get(m.name)) for m in machines]
        return [(m, *f.result()) for m, f in zip(machines, futures)]
