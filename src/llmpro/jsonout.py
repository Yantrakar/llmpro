"""One shape for every --json output: {"ok", "command", ...}."""
import json


def envelope(command, obj, ok):
    """-> {"ok": ok, "command": command, **obj} for a dict; for a list, the list is under "results"."""
    if isinstance(obj, dict):
        return {'ok': ok, 'command': command, **obj}
    return {'ok': ok, 'command': command, 'results': obj}


def emit(command, obj, ok=True):
    print(json.dumps(envelope(command, obj, ok), ensure_ascii=False))
