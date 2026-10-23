"""Update plain-text MIDI reports without accumulating repeated results."""

import os
from pathlib import Path
import re
import tempfile


def report_blocks(text):
    """Read blocks by their title, keeping the latest copy of each block."""
    blocks = {}
    title = ''
    lines = []

    def store():
        if not title and not lines:
            return
        # Closing the first-beat window is not a processing result.
        if title == 'MIDI first beat stats' and 'First beat: not changed' in lines:
            return
        blocks[title] = list(lines)

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith('MIDI ') and line.endswith((' stats', ' run')):
            store()
            title, lines = line, []
        elif line.strip():
            lines.append(line)
    store()
    return blocks


def _item_key(line):
    if ':' in line:
        return line.partition(':')[0].strip()
    step = re.match(r'^(\d+)\. ', line)
    if step:
        return step[1] + '.'
    if line.startswith('Setting ') and ' = ' in line:
        return line.partition(' = ')[0]
    return line


def update_report(report_path, new_report, replace=False):
    """Update supplied blocks line by line; optionally remove unprovided blocks."""
    path = Path(report_path)
    try:
        old_text = path.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        old_text = ''
    old_blocks = report_blocks(old_text)
    incoming = report_blocks(new_report)
    blocks = {} if replace else old_blocks.copy()
    for title, new_lines in incoming.items():
        old_items = {_item_key(line): line for line in old_blocks.get(title, [])}
        updated = {}
        for line in new_lines:
            key = _item_key(line)
            old_line = old_items.get(key)
            updated[key] = old_line if old_line == line else line
        # Follow the new report's order, omitting items no longer present.
        blocks[title] = list(updated.values())
    text = '\n\n\n'.join(
        '\n'.join(([title] if title else []) + lines)
        for title, lines in blocks.items()
    )
    if text:
        text += '\n'
    if text == old_text:
        return False
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', newline='\n',
            dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False,
        ) as output:
            temporary_path = output.name
            output.write(text)
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return True
