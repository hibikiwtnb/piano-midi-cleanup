"""Behaviour of the roll's version comparison: kinds, tail tolerance and the original's first-beat shift."""

from pathlib import Path
import sys
import tempfile
import unittest

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from version_compare import compare_versions, diff_notes, kind_counts, song_versions

DATA_ROOT = Path(__file__).resolve().parents[2] / 'piano-midi-cleanup-data'


def note(start, end, pitch):
    return dict(start=start, end=end, pitch=pitch, velocity=80)


def write_midi(path, notes, shift=0.0):
    """Notes as (start, end, pitch) seconds at 120 BPM, all moved by shift seconds."""
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    events = []
    for start, end, pitch in notes:
        events.append((round((start + shift) * 960), mido.Message('note_on', note=pitch, velocity=80)))
        events.append((round((end + shift) * 960), mido.Message('note_off', note=pitch, velocity=0)))
    tick = 0
    for event_tick, message in sorted(events, key=lambda event: (event[0], event[1].type == 'note_on')):
        track.append(message.copy(time=event_tick - tick))
        tick = event_tick
    midi.save(path)


class DiffNotes(unittest.TestCase):
    def test_kinds(self):
        reference = [note(1.0, 2.0, 60), note(2.0, 3.0, 62), note(3.0, 4.0, 64), note(4.0, 4.5, 65)]
        shown = [note(1.02, 1.5, 60), note(2.0, 3.5, 62), note(3.0, 4.0, 67), note(4.0, 4.52, 65)]
        counts = kind_counts(diff_notes(shown, reference))
        # 64 is missing and 67 extra; 65's 20 ms tail difference stays under the tolerance.
        self.assertEqual(counts, dict(extra=1, missed=1, shortened=1, lengthened=1))

    def test_shorter_tail_covers_the_removed_part(self):
        items = diff_notes([note(1.0, 1.5, 60)], [note(1.0, 2.0, 60)])
        self.assertEqual([(item['kind'], item['start'], item['end']) for item in items], [('shortened', 1.5, 2.0)])


class CompareVersions(unittest.TestCase):
    def test_original_is_aligned_by_the_first_beat_shift(self):
        DATA_ROOT.joinpath('scratch').mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=DATA_ROOT / 'scratch') as folder:
            notes = [(0.5, 1.0, 60), (1.0, 1.5, 64), (1.5, 2.0, 67)]
            write_midi(Path(folder, 'song.mid'), notes)
            write_midi(Path(folder, 'song_bpmfix.mid'), notes, shift=0.75)
            write_midi(Path(folder, 'song_bpmfix_cleaned.mid'), notes[:2], shift=0.75)
            versions = song_versions(str(Path(folder, 'song_bpmfix_cleaned.mid')))
            self.assertEqual([version.role for version in versions], ['original', 'bpmfix', 'cleaned'])
            self.assertEqual(compare_versions(versions, 'bpmfix', 'original'), [])
            missed = compare_versions(versions, 'cleaned', 'original')
            self.assertEqual([(item['kind'], item['pitch']) for item in missed], [('missed', 67)])
            self.assertAlmostEqual(missed[0]['start'], 2.25, places=2)


if __name__ == '__main__':
    unittest.main()
