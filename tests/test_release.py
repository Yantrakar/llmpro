"""The release: the version in one file, releasepro.toml and release.yml (spec 21). Reads files only."""
import re
import tomllib
from pathlib import Path

import llmpro

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / 'src' / 'llmpro' / 'VERSION'
WORKFLOW = (ROOT / '.github' / 'workflows' / 'release.yml').read_text()


def version():
    return VERSION_FILE.read_text().splitlines()[0].split()[0]


def job(name):
    """The text of one job in release.yml, up to the next job."""
    start = WORKFLOW.index(f'\n  {name}:\n')
    rest = re.search(r'\n  [a-z-]+:\n', WORKFLOW[start + 1:])
    return WORKFLOW[start:start + 1 + rest.start()] if rest else WORKFLOW[start:]


def test_the_version_file():
    first = VERSION_FILE.read_text().splitlines()[0]
    assert re.fullmatch(r'\d+\.\d+\.\d+  \d{4}-\d{2}-\d{2}  \S.*', first), first
    assert llmpro.__version__ == version()


def test_the_version_is_written_once():
    v = re.escape(version())
    files = [ROOT / 'pyproject.toml', ROOT / 'releasepro.toml', *(ROOT / '.claude-plugin').iterdir(),
             *(p for p in (ROOT / 'src').rglob('*') if p.is_file() and p != VERSION_FILE
               and '__pycache__' not in p.parts)]
    assert [p for p in files if re.search(rf'(?<![\d.]){v}(?![\d.])', p.read_text(errors='replace'))] == []


def test_pyproject_reads_the_version_file():
    hatch = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['hatch']['version']
    assert hatch['path'] == 'src/llmpro/VERSION'
    assert re.match(hatch['pattern'], VERSION_FILE.read_text())['version'] == version()


def test_releasepro_toml():
    cfg = tomllib.loads((ROOT / 'releasepro.toml').read_text())
    assert cfg['release'] == {'repo': 'Yantrakar/llmpro', 'version_file': 'src/llmpro/VERSION',
                              'branch': 'main', 'workflow': 'release.yml', 'preflight': ['gitpro why']}
    assert cfg['channel']['github']['assets'] == [
        'llmpro-{v}-py3-none-any.whl', 'llmpro-{v}.tar.gz', 'SHA256SUMS', 'ANNOUNCEMENT.md']
    assert cfg['channel']['pypi'] == {'project': 'llmpro'}
    assert set(cfg['channel']) == {'github', 'pypi'}
    assert cfg['notes']['changelog'] == 'CHANGELOG.md' and cfg['notes']['summary']


def test_the_releasepro_pin_is_written_once():
    assert re.search(r'^env:\n  RELEASEPRO: releasepro==\d+\.\d+\.\d+ ', WORKFLOW, re.M)
    assert WORKFLOW.count('releasepro==') == 1
    installs = re.findall(r'pip install .*releasepro.*|pip install "\$RELEASEPRO"', WORKFLOW)
    assert installs == ['pip install "$RELEASEPRO"'] * 2
    assert 'pip install "$RELEASEPRO"' in job('check') and 'pip install "$RELEASEPRO"' in job('build')


def test_the_jobs():
    assert 'releasepro version' in job('check') and 'releasepro notes --check' in job('check')
    assert 'releasepro notes "$VERSION" --write notes' in job('build')
    assert 'python tests/smoke_wheel.py dist/*.whl' in job('build')
    assert '    environment: release\n' in job('github')
    assert '--notes-file notes/RELEASE_NOTES.md' in job('github') and 'notes/ANNOUNCEMENT.md' in job('github')
    pypi = job('pypi')
    assert '      name: pypi\n' in pypi and 'skip-existing: true' in pypi and 'id-token: write' in pypi
    assert "if: vars.PUBLISH_MCP == 'true'" in job('mcp-registry')
    assert 'PUBLISH_PYPI' not in WORKFLOW


def test_the_changelog_has_the_version():
    assert re.search(rf'^## {re.escape(version())} \(\d{{4}}-\d{{2}}-\d{{2}}\)$',
                     (ROOT / 'CHANGELOG.md').read_text(), re.M)
