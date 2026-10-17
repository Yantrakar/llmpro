"""Run the built wheel outside the checkout, with a disposable HOME and store."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def smoke(wheel):
    with tempfile.TemporaryDirectory(prefix='agentspro-wheel-') as temporary:
        root = Path(temporary).resolve()
        package, home, store = root / 'package', root / 'home', root / 'store'
        home.mkdir(mode=0o700)
        store.mkdir()
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            archive.extractall(package)
        for template in ('AGENTS.md', 'FORMAT.md', 'config.toml'):
            assert f'agentspro/templates/{template}' in names, f'{template} is missing from the wheel'
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_') and k != 'AGENTS_STORE'}
        env.update(HOME=str(home), PYTHONPATH=str(package))

        def run(*args, text=''):
            return subprocess.run([sys.executable, '-m', 'agentspro', *args], cwd=root, env=env,
                                  input=text, capture_output=True, text=True, timeout=30)

        imported = subprocess.run([sys.executable, '-c', 'import agentspro; print(agentspro.__file__)'],
                                  cwd=root, env=env, capture_output=True, text=True, check=True, timeout=30)
        assert Path(imported.stdout.strip()) == package / 'agentspro/__init__.py', \
            'the smoke test imported the checkout instead of the wheel'

        from_wheel = zipfile.Path(wheel, 'agentspro/__init__.py').read_text()
        version = from_wheel.split('__version__ = "')[1].split('"')[0]
        result = run('--version')
        assert result.returncode == 0 and result.stdout.strip() == f'agentspro {version}', result

        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
        result = run('--store', str(store), 'mcp', text=''.join(json.dumps(r) + '\n' for r in requests))
        assert result.returncode == 0, result
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        assert replies[0]['result']['serverInfo'] == {'name': 'agentspro', 'version': version}, replies[0]
        assert {t['name'] for t in replies[1]['result']['tools']}, replies[1]


if __name__ == '__main__':
    for wheel in sys.argv[1:]:
        smoke(Path(wheel))
        print(f'{wheel}: ok')
