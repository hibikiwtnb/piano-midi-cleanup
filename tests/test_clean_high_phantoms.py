"""High phantom note cleanup removes short high notes outlasted by far-away notes, and leaves real high notes alone."""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import clean_midi_rules
import postprocess
from postprocess_panel import SWITCHES

DATA_REPO = Path(__file__).resolve().parents[2] / 'piano-midi-cleanup-data'
TICKS_PER_BEAT = 480
TEMPO = 461538


def write_midi(path, notes):
    """notes: (start beat, pitch, length in beats)."""
    events = []
    for start, pitch, length in notes:
        on, off = round(start * TICKS_PER_BEAT), round((start + length) * TICKS_PER_BEAT)
        events.append((on, 1, mido.Message('note_on', note=pitch, velocity=80)))
        events.append((off, 0, mido.Message('note_off', note=pitch)))
    midi = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    track = mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=TEMPO, time=0)])
    midi.tracks.append(track)
    now = 0
    for tick, _, message in sorted(events, key=lambda event: event[:2]):
        track.append(message.copy(time=tick - now))
        now = tick
    midi.save(path)


def pitches(path):
    return sorted(note['note'] for note in clean_midi_rules.collect_notes(mido.MidiFile(path)))


@unittest.skipUnless(DATA_REPO.is_dir(), 'needs the sibling data repository for temporary files')
class HighPhantoms(unittest.TestCase):
    def setUp(self):
        scratch = DATA_REPO / 'scratch'
        scratch.mkdir(exist_ok=True)
        holder = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name)
        self.source = self.folder / 'song_bpmfix.mid'
        self.output = self.folder / 'song_bpmfix_cleaned.mid'

    def clean(self, notes, high_phantoms=True):
        write_midi(self.source, notes)
        _, stats = clean_midi_rules.clean_midi(str(self.source), str(self.output), high_phantoms=high_phantoms)
        return pitches(self.output), stats

    def test_short_high_note_over_long_chord_is_removed(self):
        # Bar 29 of 光の中で花咲いて: E6 for 0.23 beat over a chord held 1.5 beats.
        kept, stats = self.clean([(0, 88, 0.23), (0, 64, 1.5), (0, 69, 1.5)])
        self.assertEqual(kept, [64, 69])
        self.assertEqual(stats['rule_counts']['high_phantom'], 1)

    def test_on_by_default(self):
        write_midi(self.source, [(0, 88, 0.23), (0, 64, 1.5)])
        clean_midi_rules.clean_midi(str(self.source), str(self.output))
        self.assertEqual(pitches(self.output), [64])

    def test_can_be_turned_off(self):
        kept, _ = self.clean([(0, 88, 0.23), (0, 64, 1.5)], high_phantoms=False)
        self.assertEqual(kept, [64, 88])

    def test_below_c6_stays(self):
        kept, _ = self.clean([(0, 83, 0.23), (0, 64, 1.5)])
        self.assertEqual(kept, [64, 83])

    def test_c6_is_included(self):
        kept, _ = self.clean([(0, 84, 0.23), (0, 64, 1.5)])
        self.assertEqual(kept, [64])

    def test_near_note_protects_it(self):
        kept, _ = self.clean([(0, 88, 0.23), (0, 82, 1.5), (0, 64, 1.5)])
        self.assertEqual(kept, [64, 82, 88])

    def test_a_fifth_away_counts_as_far(self):
        kept, _ = self.clean([(0, 87, 0.18), (0, 80, 1.0), (0, 68, 1.0)])
        self.assertEqual(kept, [68, 80])

    def test_short_octave_melody_stays(self):
        kept, _ = self.clean([(0, 88, 0.2), (0, 76, 0.2)])
        self.assertEqual(kept, [76, 88])

    def test_other_note_exactly_a_sixteenth_does_not_count_as_longer(self):
        kept, _ = self.clean([(0, 88, 0.2), (0, 76, 0.25)])
        self.assertEqual(kept, [76, 88])

    def test_longer_high_note_stays(self):
        kept, _ = self.clean([(0, 88, 0.5), (0, 64, 1.5)])
        self.assertEqual(kept, [64, 88])

    def test_lone_short_high_note_stays(self):
        kept, _ = self.clean([(0, 88, 0.2), (2, 64, 1.5)])
        self.assertEqual(kept, [64, 88])

    def test_note_struck_within_the_window_counts(self):
        kept, _ = self.clean([(0, 88, 0.2), (0.08, 64, 1.5)])
        self.assertEqual(kept, [64])

    def test_note_struck_outside_the_window_does_not_count(self):
        kept, _ = self.clean([(0, 88, 0.2), (0.25, 64, 1.5)])
        self.assertEqual(kept, [64, 88])

    def test_report_shows_the_rule_only_when_enabled(self):
        _, enabled = self.clean([(0, 88, 0.23), (0, 64, 1.5)])
        _, disabled = self.clean([(0, 88, 0.23), (0, 64, 1.5)], high_phantoms=False)
        self.assertIn('High phantom note', clean_midi_rules.format_clean_stats(enabled))
        self.assertNotIn('High phantom note', clean_midi_rules.format_clean_stats(disabled))

    def test_pipeline_flag_reaches_rule_cleaning(self):
        write_midi(self.source, [(0, 88, 0.23), (0, 64, 1.5)])
        args = [str(self.source), '--no-key-signature', '--no-split', '--no-trim']
        with contextlib.redirect_stdout(io.StringIO()):
            postprocess.main(args)
        self.assertEqual(pitches(self.output), [64])
        write_midi(self.source, [(0, 88, 0.23), (0, 64, 1.5)])
        with contextlib.redirect_stdout(io.StringIO()):
            postprocess.main(args + ['--no-high-phantoms'])
        self.assertEqual(pitches(self.output), [64, 88])

    def test_panel_switch_is_on_by_default(self):
        self.assertTrue(dict((name, default) for name, _, default in SWITCHES)['high_phantoms'])


if __name__ == '__main__':
    unittest.main()
