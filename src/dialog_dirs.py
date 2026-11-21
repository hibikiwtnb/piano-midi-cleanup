"""Starting folders for file dialogs: the folder of the current choice, else the last folder used, else home."""

import os
from pathlib import Path

LAST_DIR_FILE = Path.home() / '.local' / 'share' / 'piano-midi-cleanup' / 'last_dialog_dir.txt'


def initial_dir(current=''):
    candidates = [current]
    try:
        candidates.append(LAST_DIR_FILE.read_text(encoding='utf-8').strip())
    except OSError:
        pass
    for candidate in candidates:
        if candidate:
            folder = candidate if os.path.isdir(candidate) else os.path.dirname(candidate)
            if folder and os.path.isdir(folder):
                return folder
    return str(Path.home())


def remember(result):
    """Store the folder of a dialog's result (a path, a tuple of paths, or '') and return it unchanged."""
    first = result[0] if isinstance(result, (tuple, list)) and result else result
    if first:
        folder = first if os.path.isdir(first) else os.path.dirname(first)
        try:
            LAST_DIR_FILE.parent.mkdir(parents=True, exist_ok=True)
            LAST_DIR_FILE.write_text(folder, encoding='utf-8')
        except OSError:
            pass
    return result
