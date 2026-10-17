"""Finding outside programs. Everything llmpro runs except `git` and the running Python (gitpro, claude, codex)
is found through `which`, so tests can point it at a temporary folder."""
import os
import shutil


def which(name):
    """-> the path of the program, or None. If $LLMPRO_TOOL_PATH is set (a list of folders, possibly empty),
    look only there; otherwise use PATH."""
    folders = os.environ.get('LLMPRO_TOOL_PATH')
    if folders is None:
        return shutil.which(name)
    return shutil.which(name, path=folders)
