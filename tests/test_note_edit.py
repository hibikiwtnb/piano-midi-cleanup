"""Behaviour of the roll's note editing: indices follow the roll's note order, deletion keeps later timing."""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from note_edit import edit_notes, paired_notes
from pianoroll_canvas import read_roll

DATA_ROOT = Path(__file__).resolve().parents[2] / 'piano-midi-cleanup-data'


def write_midi(path, notes):
    """Notes as (start_tick, end_tick, pitch, channel)."""
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    events = []
    for start, end, pitch, channel in notes:
        events.append((start, 1, mido.Message('note_on', note=pitch, velocity=80, channel=channel)))
        events.append((end, 0, mido.Message('note_off', note=pitch, velocity=0, channel=channel)))
    tick = 0
    for event_tick, _, message in sorted(events, key=lambda event: event[:2]):
        track.append(message.copy(time=event_tick - tick))
        tick = event_tick
    midi.save(path)


def ticks(path):
    return sorted((note.start_tick, note.end_tick, note.pitch, note.channel)
                  for note in paired_notes(mido.MidiFile(path)))


class NoteEdit(unittest.TestCase):
    def setUp(self):
        DATA_ROOT.joinpath('scratch').mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.mkdtemp(dir=DATA_ROOT / 'scratch')
        self.path = str(Path(self.folder, 'song_bpmfix.mid'))
        # Includes overlapping repeats of one pitch, which pair last-in, first-out.
        write_midi(self.path, [(0, 480, 60, 0), (240, 960, 64, 1), (480, 720, 60, 0), (600, 1200, 60, 0),
                               (960, 1440, 67, 1)])

    def tearDown(self):
        shutil.rmtree(self.folder)

    def test_indices_follow_the_roll_order(self):
        roll = [(start, pitch, channel) for start, _, pitch, channel, _ in read_roll(self.path)[0]]
        midi = mido.MidiFile(self.path)
        seconds = 60 / 120 / 480
        paired = [(note.start_tick * seconds, note.pitch, note.channel) for note in paired_notes(midi)]
        self.assertEqual([(round(start, 6), pitch, channel) for start, pitch, channel in roll],
                         [(round(start, 6), pitch, channel) for start, pitch, channel in paired])

    def test_delete_keeps_the_other_notes_in_place(self):
        before = ticks(self.path)
        target = list(paired_notes(mido.MidiFile(self.path)))[1]
        edit_notes(self.path, [1])
        removed = (target.start_tick, target.end_tick, target.pitch, target.channel)
        self.assertEqual(ticks(self.path), [note for note in before if note != removed])

    def test_moving_to_another_channel_changes_only_the_channel(self):
        before = ticks(self.path)
        edit_notes(self.path, channels={0: 3, 4: 3})
        notes = list(paired_notes(mido.MidiFile(self.path)))
        self.assertEqual(sorted((n.start_tick, n.end_tick, n.pitch) for n in notes),
                         sorted(note[:3] for note in before))
        self.assertEqual(sum(note.channel == 3 for note in notes), 2)

    def test_delete_and_move_in_one_pass(self):
        before = ticks(self.path)
        edit_notes(self.path, deleted=[1], channels={4: 3})
        after = ticks(self.path)
        self.assertEqual(len(after), len(before) - 1)
        self.assertEqual(sum(note[3] == 3 for note in after), 1)


if __name__ == '__main__':
    unittest.main()
