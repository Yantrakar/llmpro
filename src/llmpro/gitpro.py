"""The gitpro adapter. llmpro never imports gitpro: it finds the program, reads `gitpro --version --json` and
runs gitpro commands with `--json`. A missing or too old gitpro hides the git features; it never breaks llmpro."""
import json
import os
import subprocess

from . import tools
from .plugins.base import Status

MIN_VERSION = (0, 4, 0)


class GitproError(Exception):
    pass


def parse_version(text):
    """'0.4.1rc1' -> (0, 4, 1): the first three dot-separated numbers; anything after a number is ignored."""
    out = []
    for part in str(text).split('.')[:3]:
        digits = ''
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        out.append(int(digits))
    while len(out) < 3:
        out.append(0)
    return tuple(out)


class Gitpro:
    version_timeout = 10

    def __init__(self):
        self.path = self._find()
        self.version = None
        self.capabilities = frozenset()
        self._status = None

    @staticmethod
    def _find():
        env = os.environ.get('LLMPRO_GITPRO')
        if env is not None:
            return env if env and os.path.isfile(env) and os.access(env, os.X_OK) else None
        return tools.which('gitpro')

    def _env(self):
        return {**os.environ, 'NO_COLOR': '1', 'GITPRO_NO_DEPRECATION': '1'}

    def status(self):
        if self._status is None:
            self._status = self._probe()
        return self._status

    def _probe(self):
        if not self.path:
            return Status('missing', 'gitpro is not installed', 'pipx install gitpro, or pipx install "llmpro[git]"')

        def too_old(why):
            return Status('too_old', why, 'pipx upgrade gitpro')
        try:
            r = subprocess.run([self.path, '--version', '--json'], stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=self.version_timeout, env=self._env())
        except subprocess.TimeoutExpired:
            return too_old('gitpro --version --json timed out')
        except OSError as exc:
            return too_old(f'gitpro could not be run: {exc}')
        try:
            data = json.loads(r.stdout)
            if not isinstance(data, dict):
                raise ValueError('not an object')
            version = str(data['version'])
            caps = data.get('capabilities') or []
            if not isinstance(caps, list):
                raise ValueError('capabilities is not a list')
        except (ValueError, KeyError):
            return too_old('gitpro does not answer --version --json (too old)')
        self.version = version
        self.capabilities = frozenset(str(c) for c in caps)
        if parse_version(version) < MIN_VERSION:
            return too_old(f'gitpro {version} is older than {".".join(map(str, MIN_VERSION))}')
        return Status('ok')

    def has(self, capability):
        return self.status().state == 'ok' and capability in self.capabilities

    def run_json(self, args, timeout=30):
        """-> (exit code, the one JSON object gitpro printed). Raises GitproError if gitpro is missing, times out,
        or does not print one JSON object."""
        if not self.path:
            raise GitproError('gitpro is not installed')
        try:
            r = subprocess.run([self.path, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=timeout, env=self._env())
        except subprocess.TimeoutExpired:
            raise GitproError(f'gitpro {" ".join(args)} timed out after {timeout:g} s') from None
        except OSError as exc:
            raise GitproError(f'gitpro could not be run: {exc}') from None
        try:
            data = json.loads(r.stdout)
        except ValueError:
            raise GitproError(f'gitpro {" ".join(args)} did not print JSON') from None
        if not isinstance(data, dict):
            raise GitproError(f'gitpro {" ".join(args)} did not print one JSON object')
        return r.returncode, data

    def run(self, args):
        """Run gitpro with the terminal passed through. -> its exit code."""
        if not self.path:
            raise GitproError('gitpro is not installed')
        try:
            return subprocess.run([self.path, *args], env=self._env()).returncode
        except OSError as exc:
            raise GitproError(f'gitpro could not be run: {exc}') from None


_cache = []


def get():
    if not _cache:
        _cache.append(Gitpro())
    return _cache[0]


def reset_cache():
    _cache.clear()
