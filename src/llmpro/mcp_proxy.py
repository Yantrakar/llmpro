"""The MCP children behind `llmpro mcp`: a plugin that offers a child (today `gitpro mcp`) has its read-only tools
served under the plugin's prefix. Standard library only. The server stays single-threaded; each child has one
reader thread that only moves its output lines into a queue, so a request can wait with a timeout."""
import json
import os
import queue
import subprocess
import sys
import threading
import time

PROTOCOL_VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
MAX_STARTS = 4  # the first start and three restarts per server run
DEFAULT_TIMEOUT = 30.0


class ChildError(Exception):
    """The child could not be started or did not answer; the message is one line for the tool result."""


class RpcError(Exception):
    """The child answered a request with a JSON-RPC error."""


def timeout():
    try:
        value = float(os.environ.get('LLMPRO_MCP_CHILD_TIMEOUT', ''))
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def safe_argv(argv):
    """The child never runs with --allow-execution, whatever a plugin passes."""
    return [a for a in argv if a != '--allow-execution']


def _stderr():
    try:
        sys.stderr.fileno()
        return sys.stderr
    except (AttributeError, OSError, ValueError):
        return None  # the child inherits llmpro's own stderr


def _version():
    from . import __version__
    return __version__


class Child:
    def __init__(self, name, argv, protocol):
        self.name = name
        self.argv = safe_argv(argv)
        self.protocol = protocol if protocol in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1]
        self.starts = 0
        self._proc = None
        self._lines = None
        self._tools = []
        self._next_id = 0

    # -- process handling
    def _alive(self):
        return self._proc is not None and self._proc.poll() is None

    def _kill(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
        proc.wait()  # kill first: closing stdout while the reader thread waits in it would block
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    @staticmethod
    def _read(proc, lines):
        try:
            for line in proc.stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        lines.put(None)

    def _start(self):
        if self.starts >= MAX_STARTS:
            raise ChildError(f'the {self.name} tools are unavailable (the child failed {MAX_STARTS} times)')
        self.starts += 1
        try:
            self._proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=_stderr(), text=True, encoding='utf-8', bufsize=1)
        except OSError as exc:
            raise ChildError(f'the {self.name} child could not be started: {exc}') from None
        self._lines = queue.Queue()
        threading.Thread(target=self._read, args=(self._proc, self._lines), daemon=True).start()
        try:
            got = self.request('initialize', {'protocolVersion': self.protocol, 'capabilities': {},
                                              'clientInfo': {'name': 'llmpro', 'version': _version()}})
            if got.get('protocolVersion') not in PROTOCOL_VERSIONS:
                raise ChildError(f'the {self.name} child speaks protocol {got.get("protocolVersion")}, '
                                 'which llmpro does not support')
            self.notify('notifications/initialized')
            self._tools = list(self.request('tools/list', {}).get('tools') or [])
        except RpcError as exc:
            self._kill()
            raise ChildError(f'the {self.name} child refused to start: {exc}') from None
        except ChildError:
            self._kill()
            raise

    def _ensure(self):
        if not self._alive():
            self._kill()
            self._start()

    # -- messages
    def _send(self, msg):
        try:
            self._proc.stdin.write(json.dumps(msg) + '\n')
            self._proc.stdin.flush()
        except (OSError, ValueError, AttributeError):
            self._kill()
            raise ChildError(f'the {self.name} child is not running') from None

    def notify(self, method, params=None):
        if not self._alive():
            return
        msg = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            msg['params'] = params
        try:
            self._send(msg)
        except ChildError:
            pass

    def request(self, method, params):
        """-> the child's result dict. Raises ChildError, or RpcError for an error answer."""
        self._next_id += 1
        mid = self._next_id
        self._send({'jsonrpc': '2.0', 'id': mid, 'method': method, 'params': params})
        limit = timeout()
        deadline = time.monotonic() + limit
        while True:
            try:
                line = self._lines.get(timeout=max(deadline - time.monotonic(), 0))
            except queue.Empty:
                self._kill()
                raise ChildError(f'the {self.name} child timed out after {limit:g} s') from None
            if line is None:
                self._kill()
                raise ChildError(f'the {self.name} child exited')
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
                if not isinstance(msg, dict):
                    raise ValueError
            except ValueError:
                self._kill()
                raise ChildError(f'the {self.name} child answered something that is not JSON-RPC') from None
            if msg.get('id') != mid:
                continue  # a notification or a late answer
            if isinstance(msg.get('error'), dict):
                raise RpcError(str(msg['error'].get('message') or 'error'))
            result = msg.get('result')
            if not isinstance(result, dict):
                self._kill()
                raise ChildError(f'the {self.name} child answered without a result')
            return result

    # -- use
    def tools(self):
        self._ensure()
        return self._tools

    def call(self, tool, arguments):
        self._ensure()
        try:
            return self.request('tools/call', {'name': tool, 'arguments': arguments})
        except RpcError as exc:
            return error_result(str(exc))

    def close(self):
        proc = self._proc
        if proc is None:
            return
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        self._kill()


def error_result(text):
    return {'content': [{'type': 'text', 'text': text}], 'isError': True}


class Proxy:
    """Every plugin's child, started on first need."""

    def __init__(self, store, protocol=None):
        from . import plugins, store as cfg
        from .plugins.base import Context
        self.children = {}  # plugin name -> Child
        self.missing = []  # one sentence per plugin whose tools are not offered
        ctx = Context(store=store, home=cfg.home())
        for p in plugins.load():
            try:
                argv = p.mcp_child(ctx)
                if argv:
                    self.children[p.name] = Child(p.name, list(argv), protocol)
                    continue
                st = p.status(ctx)
            except Exception as exc:
                self.missing.append(f'The {p.name} tools are not offered: {type(exc).__name__}: {exc}.')
                continue
            if st.state != 'ok':
                text = f'The {p.name} tools are not offered: {st.reason}.'
                self.missing.append(text + (f' To get them: {st.hint}.' if st.hint else ''))

    def set_protocol(self, protocol):
        for c in self.children.values():
            c.protocol = protocol

    def _table(self, taken):
        """-> {listed name: (child, the child's tool name, the tool as listed)}"""
        table = {}
        taken = set(taken)
        for name, child in self.children.items():
            try:
                found = child.tools()
            except ChildError as exc:
                print(f'llmpro mcp: {exc}', file=sys.stderr)
                continue
            for t in found:
                if not isinstance(t, dict) or not isinstance(t.get('name'), str):
                    continue
                if str(t.get('description') or '').lstrip().lower().startswith('deprecated'):
                    continue
                ann = t.get('annotations')
                if not isinstance(ann, dict) or ann.get('readOnlyHint') is not True:
                    continue  # only tools that say they read and nothing more
                listed = f'{name}_{t["name"]}'
                if listed in taken:
                    continue
                taken.add(listed)
                tool = dict(t, name=listed)
                if 'title' in ann:
                    tool['annotations'] = dict(ann, title=f'{name}: {ann["title"]}')
                table[listed] = (child, t['name'], tool)
        return table

    def list_tools(self, own_names):
        return [entry[2] for entry in self._table(own_names).values()]

    def owns(self, tool_name):
        return any(tool_name.startswith(f'{name}_') for name in self.children)

    def call(self, own_names, tool_name, arguments):
        """-> the tools/call result; KeyError if the tool is not listed."""
        prefix = next(name for name in self.children if tool_name.startswith(f'{name}_'))
        child = self.children[prefix]
        try:
            child.tools()
        except ChildError as exc:
            return error_result(str(exc))
        entry = self._table(own_names).get(tool_name)
        if entry is None:
            raise KeyError(tool_name)
        try:
            return child.call(entry[1], arguments)
        except ChildError as exc:
            return error_result(str(exc))

    def cancel(self, params):
        for child in self.children.values():
            child.notify('notifications/cancelled', params)

    def close(self):
        for child in self.children.values():
            child.close()
