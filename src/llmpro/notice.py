"""A desktop notice through `notify-send`, when this machine has it. Nothing happens otherwise."""
import subprocess

from . import tools


def notify(title, msg, icon=None):
    """Send one notice. Does nothing when notify-send is not found; never raises."""
    send = tools.which('notify-send')
    if not send:
        return
    argv = [send, '--app-name=llmpro'] + ([f'--icon={icon}'] if icon else []) + [title, msg]
    try:
        subprocess.run(argv, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
