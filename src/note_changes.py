"""Record visual-only note changes alongside a post-processed MIDI."""

from collections import defaultdict
import hashlib
import json
from pathlib import Path

import mido

from clean_midi_rules import tick_seconds
from hand_split_channels import collect_notes


def changes_path(midi_path):
    return Path(midi_path).with_suffix('.changes.json')


def fingerprint(midi_path):
    return hashlib.sha256(Path(midi_path).read_bytes()).hexdigest()


def note_snapshot(midi_path):
    midi = mido.MidiFile(midi_path)
    seconds = tick_seconds(midi)
    return [dict(start_tick=note['start_ticks'], end_tick=note['end_ticks'],
                 start=seconds(note['start_ticks']), end=seconds(note['end_ticks']),
                 pitch=note['note'], velocity=note['velocity'], channel=note['start_message'].channel)
            for note in collect_notes(midi)]


def stage_changes(before, after, stage):
    """Match exact note identities, allowing only the changes made by this stage."""
    def key(note):
        identity = (note['start_tick'], note['pitch'], note['velocity'])
        return identity + ((note['channel'],) if stage == 'trim' else (note['end_tick'],))

    remaining = defaultdict(list)
    for note in after:
        remaining[key(note)].append(note)
    changes = []
    for note in before:
        matches = remaining[key(note)]
        if matches:
            # Prefer unchanged ends for simultaneous duplicate notes.
            index = next((i for i, other in enumerate(matches) if other['end_tick'] == note['end_tick']), 0)
            other = matches.pop(index)
            if stage == 'trim' and other['end_tick'] < note['end_tick']:
                changes.append(dict(kind='shortened', stage=stage, start=other['end'], end=note['end'],
                                    pitch=note['pitch'], velocity=note['velocity']))
        elif stage != 'trim':
            changes.append(dict(kind='deleted', stage=stage, start=note['start'], end=note['end'],
                                pitch=note['pitch'], velocity=note['velocity']))
    return changes


def save_changes(midi_path, changes):
    payload = dict(version=1, midi_sha256=fingerprint(midi_path), changes=changes)
    destination = changes_path(midi_path)
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=True), encoding='utf-8')
    temporary.replace(destination)


def load_changes(midi_path):
    """Reject stale records rather than displaying changes against a different MIDI."""
    try:
        payload = json.loads(changes_path(midi_path).read_text(encoding='utf-8'))
        if payload['version'] == 1 and payload['midi_sha256'] == fingerprint(midi_path):
            changes = payload['changes']
            if isinstance(changes, list) and all(
                item['kind'] in ('deleted', 'shortened')
                and item['stage'] in ('clean', 'playable', 'trim')
                and 0 <= item['pitch'] <= 127 and 0 <= item['start'] <= item['end']
                for item in changes
            ):
                return changes
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None
