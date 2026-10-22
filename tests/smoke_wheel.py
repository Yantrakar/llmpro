"""Run the built wheel outside the checkout, with a disposable HOME and store."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def smoke(wheel):
    with tempfile.TemporaryDirectory(prefix='llmpro-wheel-') as temporary:
        root = Path(temporary).resolve()
        package, home, store = root / 'package', root / 'home', root / 'store'
        home.mkdir(mode=0o700)
        store.mkdir()
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            archive.extractall(package)
        for template in ('AGENTS.md', 'FORMAT.md', 'config.toml'):
            assert f'llmpro/templates/{template}' in names, f'{template} is missing from the wheel'
        assert 'llmpro/skills/llmpro/SKILL.md' in names, 'the skill is missing from the wheel'
        for name in ('memory', 'secrets', 'git'):
            assert f'llmpro/skills/llmpro/references/{name}.md' in names, f'reference {name} is missing from the wheel'
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_') and k != 'AGENTS_STORE'}
        env.update(HOME=str(home), PYTHONPATH=str(package))

        def run(*args, text=''):
            return subprocess.run([sys.executable, '-m', 'llmpro', *args], cwd=root, env=env,
                                  input=text, capture_output=True, text=True, timeout=30)

        imported = subprocess.run([sys.executable, '-c', 'import llmpro; print(llmpro.__file__)'],
                                  cwd=root, env=env, capture_output=True, text=True, check=True, timeout=30)
        assert Path(imported.stdout.strip()) == package / 'llmpro/__init__.py', \
            'the smoke test imported the checkout instead of the wheel'

        assert 'llmpro/VERSION' in names, 'VERSION is missing from the wheel'
        version = zipfile.Path(wheel, 'llmpro/VERSION').read_text().split()[0]
        result = run('--version')
        assert result.returncode == 0 and result.stdout.strip() == f'llmpro {version}', result

        entry_points = next(n for n in names if n.endswith('.dist-info/entry_points.txt'))
        scripts = zipfile.Path(wheel, entry_points).read_text()
        assert 'llmpro = llmpro.cli:main' in scripts and 'agentspro' not in scripts, scripts
        result = run('--version', '--json')
        assert result.returncode == 0 and json.loads(result.stdout)['command'] == 'version', result

        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
        result = run('--store', str(store), 'mcp', text=''.join(json.dumps(r) + '\n' for r in requests))
        assert result.returncode == 0, result
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        assert replies[0]['result']['serverInfo'] == {'name': 'llmpro', 'version': version}, replies[0]
        assert {t['name'] for t in replies[1]['result']['tools']}, replies[1]

        # the skill setup item reads the copy inside the wheel (no outside program is found: empty tool path)
        empty = root / 'no-tools'
        empty.mkdir()
        env['LLMPRO_TOOL_PATH'] = str(empty)
        result = run('--store', str(store), 'setup', '-y', '--only', 'memory')
        assert result.returncode == 0, result
        installed = store / 'skills' / 'llmpro' / 'SKILL.md'
        assert installed.is_file() and 'installed by llmpro setup' in installed.read_text(), result


if __name__ == '__main__':
    for wheel in sys.argv[1:]:
        smoke(Path(wheel))
        print(f'{wheel}: ok')
