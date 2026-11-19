"""Fast repeated-chord merging keeps the first strike of a 16th-note repeat and leaves real repeats alone."""

from pathlib import Path
import sys
import tempfile
import unittest

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hand_split_channels import CHANNELS, collect_notes
import playable_reduction

DATA_REPO = Path(__file__).resolve().parents[2] / 'piano-midi-cleanup-data'
TICKS_PER_BEAT = 480
RIGHT, LEFT, SECOND_RIGHT = CHANNELS['right'], CHANNELS['left'], CHANNELS['second_right']


def write_midi(path, notes):
    """notes: (start beat, pitch, length in beats), optionally followed by a channel."""
    events = []
    for start, pitch, length, *channel in notes:
        channel = channel[0] if channel else RIGHT
        on, off = round(start * TICKS_PER_BEAT), round((start + length) * TICKS_PER_BEAT)
        events.append((on, 1, mido.Message('note_on', channel=channel, note=pitch, velocity=80)))
        events.append((off, 0, mido.Message('note_off', channel=channel, note=pitch)))
    midi = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    track = mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=500000, time=0)])
    midi.tracks.append(track)
    now = 0
    for tick, _, message in sorted(events, key=lambda event: event[:2]):
        track.append(message.copy(time=tick - now))
        now = tick
    midi.save(path)


def repeated_chord(chord, starts, length=0.2):
    return [(start, pitch, length) for start in starts for pitch in chord]


class FastChordTest(unittest.TestCase):
    def setUp(self):
        DATA_REPO.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=DATA_REPO)
        self.path = str(Path(self.folder.name) / 'song.mid')

    def tearDown(self):
        self.folder.cleanup()

    def reduce(self, notes, merging=True, **options):
        write_midi(self.path, notes)
        stats = playable_reduction.reduce_in_place(self.path, merge_fast_chords=merging, **options)
        return stats, collect_notes(mido.MidiFile(self.path))

    def test_a_fast_repeated_chord_keeps_only_the_first_strike(self):
        chord = [62, 65, 69]
        stats, notes = self.reduce(repeated_chord(chord, (0, 0.25, 0.5, 0.75)))
        self.assertEqual(sorted(note['note'] for note in notes), chord)
        for note in notes:
            self.assertEqual(note['start_ticks'], 0)
            self.assertAlmostEqual(note['end_ticks'] / TICKS_PER_BEAT, 0.95, delta=0.005)
        self.assertEqual(len(stats['fast_chord_merges']), 1)
        self.assertEqual(stats['fast_chord_merges'][0]['repeats'], 3)
        self.assertEqual(stats['fast_chord_merges'][0]['removed'], 9)

    def test_a_repeat_past_the_window_is_not_merged(self):
        chord = [62, 65, 69]
        stats, notes = self.reduce(repeated_chord(chord, (0, 0.5)))
        self.assertEqual(len(notes), 6)
        self.assertEqual(stats['fast_chord_merges'], [])

    def test_a_different_chord_is_not_merged(self):
        notes = repeated_chord([62, 65, 69], (0,)) + repeated_chord([62, 65, 70], (0.25,))
        stats, result = self.reduce(notes)
        self.assertEqual(len(result), 6)
        self.assertEqual(stats['fast_chord_merges'], [])

    def test_a_single_note_is_not_a_chord(self):
        stats, notes = self.reduce(repeated_chord([69], (0, 0.25)))
        self.assertEqual(len(notes), 2)
        self.assertEqual(stats['fast_chord_merges'], [])

    def test_the_tail_stops_at_the_next_onset(self):
        # The last repeat would run past the next chord, so the earlier repeat's end is the one kept.
        notes = repeated_chord([62, 65, 69], (0, 0.15), length=0.1)
        notes += repeated_chord([62, 65, 69], (0.3,), length=0.5)
        notes += [(0.45, 74, 0.2)]
        stats, result = self.reduce(notes)
        kept = [note for note in result if note['note'] in (62, 65, 69)]
        for note in kept:
            self.assertAlmostEqual(note['end_ticks'] / TICKS_PER_BEAT, 0.25, delta=0.005)
        self.assertEqual(len(stats['fast_chord_merges']), 1)

    def test_the_flag_off_output_is_unchanged(self):
        write_midi(self.path, repeated_chord([62, 65, 69], (0, 0.25, 0.5, 0.75)))
        before = Path(self.path).read_bytes()
        stats = playable_reduction.reduce_in_place(self.path, merge_fast_chords=False)
        self.assertEqual(Path(self.path).read_bytes(), before)
        self.assertFalse(stats['merge_fast_chords'])
        self.assertNotIn('Fast repeated chords', playable_reduction.format_reduction_stats(stats))

    def test_the_report_lists_the_merged_place(self):
        stats, _ = self.reduce(repeated_chord([62, 65, 69], (0, 0.25)))
        report = playable_reduction.format_reduction_stats(stats)
        self.assertIn('Fast repeated chords: 1 places, 1 repeated strikes, 3 notes removed', report)
        self.assertIn('D4 F4 A4 repeated 2 times', report)

    def test_three_hands_leave_the_second_player_alone(self):
        notes = []
        for start in (0, 0.25):
            notes += [(start, 48, 0.2, LEFT), (start, 52, 0.2, LEFT)]
            notes += [(start, 60, 0.2, RIGHT), (start, 64, 0.2, RIGHT)]
            notes += [(start, 72, 0.2, SECOND_RIGHT), (start, 76, 0.2, SECOND_RIGHT)]
        stats, result = self.reduce(notes, hands=playable_reduction.THREE_HAND_PLAYER)
        first_player = [note for note in result if note['start_message'].channel in (LEFT, RIGHT)]
        second_player = [note for note in result if note['start_message'].channel == SECOND_RIGHT]
        self.assertEqual(len(first_player), 4)
        self.assertEqual(len(second_player), 4)
        self.assertEqual(len(stats['fast_chord_merges']), 1)


if __name__ == '__main__':
    unittest.main()
