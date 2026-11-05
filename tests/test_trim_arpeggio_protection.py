"""Arpeggio protection keeps a slightly spread chord whole, and overlap trim still shortens what only looks like one."""

from pathlib import Path
import sys
import tempfile
import unittest

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hand_split_channels import CHANNELS, collect_notes
import trim_overlaps

DATA_REPO = Path(__file__).resolve().parents[2] / 'piano-midi-cleanup-data'
TICKS_PER_BEAT = 480
RIGHT, LEFT = CHANNELS['right'], CHANNELS['left']


def write_midi(path, notes):
    """notes: (start beat, pitch, length in beats), optionally followed by a channel."""
    events = []
    for start, pitch, length, *channel in notes:
        channel = channel[0] if channel else RIGHT
        on, off = round(start * TICKS_PER_BEAT), round((start + length) * TICKS_PER_BEAT)
        events.append((on, 1, mido.Message('note_on', channel=channel, note=pitch, velocity=80)))
        events.append((off, 0, mido.Message('note_off', channel=channel, note=pitch)))
    midi = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    track = mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=461538, time=0)])
    midi.tracks.append(track)
    now = 0
    for tick, _, message in sorted(events, key=lambda event: event[:2]):
        track.append(message.copy(time=tick - now))
        now = tick
    midi.save(path)


@unittest.skipUnless(DATA_REPO.is_dir(), 'needs the sibling data repository for temporary files')
class ArpeggioProtection(unittest.TestCase):
    def setUp(self):
        scratch = DATA_REPO / 'scratch'
        scratch.mkdir(exist_ok=True)
        holder = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(holder.cleanup)
        self.path = Path(holder.name) / 'song.mid'

    def trim(self, notes, **options):
        """Return the trim stats and each pitch's length in beats after trimming."""
        write_midi(self.path, notes)
        options.setdefault('protect_pedal', False)
        stats = trim_overlaps.trim_overlaps_in_place(str(self.path), **options)
        lengths = {note['note']: (note['end_ticks'] - note['start_ticks']) / TICKS_PER_BEAT
                   for note in collect_notes(mido.MidiFile(self.path))}
        return stats, lengths

    def test_three_onset_chord_is_not_trimmed_inside_itself(self):
        # The opening of 光の中で花咲いて: on its own it meets the fast figure's minimum of three onset groups.
        stats, lengths = self.trim([(0, 71, 4), (0.112, 76, 4), (0.232, 80, 4)])
        self.assertAlmostEqual(lengths[71], 4, delta=0.005)
        self.assertAlmostEqual(lengths[76], 4, delta=0.005)
        self.assertEqual(stats['arpeggio_kept'], 2)

    def test_two_onset_chord_counts(self):
        _, lengths = self.trim([(0, 67, 2), (0, 74, 2), (0.15, 62, 2), (0.6, 79, 2), (1.1, 84, 2)])
        self.assertGreater(lengths[67], 0.25)
        self.assertGreater(lengths[74], 0.25)

    def test_pedal_protection_does_not_change_the_decision(self):
        stats, lengths = self.trim([(0, 71, 4), (0.112, 76, 4), (0.232, 80, 4)], protect_pedal=True)
        self.assertAlmostEqual(lengths[71], 4, delta=0.005)
        self.assertEqual(stats['arpeggio_kept'], 2)
        self.assertEqual(stats['sustain_kept'], 0)

    def test_later_trims_still_apply(self):
        _, lengths = self.trim([(0, 60, 4), (0.12, 64, 4), (0.24, 67, 4), (2, 61, 1), (2, 65, 1), (2, 68, 1)])
        self.assertAlmostEqual(lengths[60], 2, delta=0.005)
        self.assertAlmostEqual(lengths[64], 1.88, delta=0.005)
        self.assertAlmostEqual(lengths[67], 1.76, delta=0.005)

    def test_sixteenth_line_is_still_trimmed(self):
        # Gaps just under a sixteenth, as real sixteenths have in a transcription, in pairs that fit the span.
        notes = [(0, 60, 2), (0.24, 64, 2), (0.48, 67, 2), (0.72, 72, 2)]
        stats, lengths = self.trim(notes)
        self.assertAlmostEqual(lengths[60], 0.24, delta=0.005)
        self.assertAlmostEqual(lengths[64], 0.24, delta=0.005)
        self.assertEqual(stats['arpeggio_kept'], 0)

    def test_sixteenth_after_a_long_note_is_not_a_chord(self):
        # Dotted rhythm: the last two onsets fit the span, but their gap is a real sixteenth.
        _, lengths = self.trim([(0, 60, 2), (0.5, 64, 2), (0.75, 67, 2), (1.25, 72, 2)])
        self.assertAlmostEqual(lengths[64], 0.25, delta=0.005)

    def test_glissando_is_still_trimmed(self):
        pitches = [96, 91, 86, 81, 76, 71]
        stats, lengths = self.trim([(index * 0.15, pitch, 2) for index, pitch in enumerate(pitches)])
        for pitch in pitches[:-1]:
            self.assertAlmostEqual(lengths[pitch], 0.15, delta=0.005)
        self.assertEqual(stats['arpeggio_kept'], 0)

    def test_neighbour_tone_is_still_trimmed(self):
        _, lengths = self.trim([(0, 73, 1), (0, 78, 1), (0.14, 79, 1), (0.5, 76, 1)])
        self.assertAlmostEqual(lengths[73], 0.14, delta=0.005)
        self.assertAlmostEqual(lengths[78], 0.14, delta=0.005)

    def test_two_pitches_are_not_a_chord(self):
        _, lengths = self.trim([(0, 60, 1), (0.12, 72, 1), (0.5, 65, 1)])
        self.assertAlmostEqual(lengths[60], 0.12, delta=0.005)

    def test_other_hand_does_not_complete_a_chord(self):
        _, lengths = self.trim([(0, 60, 1), (0.12, 67, 1), (0.12, 40, 1, LEFT), (0.5, 72, 1)])
        self.assertAlmostEqual(lengths[60], 0.12, delta=0.005)

    def test_report_counts_kept_notes(self):
        stats, _ = self.trim([(0, 71, 4), (0.112, 76, 4), (0.232, 80, 4)])
        self.assertIn('Notes kept by arpeggio protection: 2', trim_overlaps.format_trim_stats(stats))


if __name__ == '__main__':
    unittest.main()
