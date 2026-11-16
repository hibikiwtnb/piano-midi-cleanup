"""Automatic time signature: inferred from the performance, or left undetermined instead of guessed."""

from pathlib import Path
import sys
import tempfile
import unittest

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from bpmfix_utils import apply_bpm_fix
from meter_detect import detect_meter

DATA_REPO = Path(__file__).resolve().parents[2] / 'piano-midi-cleanup-data'
TICKS_PER_BEAT = 384
BEATS_PER_SECOND = 2
TEMPO = int(1e6 // BEATS_PER_SECOND)


def write_midi(path, notes):
    """notes: (start seconds, pitch, velocity); written like a fresh transcription MIDI."""
    events = []
    for start, pitch, velocity in notes:
        tick = round(start * TICKS_PER_BEAT * BEATS_PER_SECOND)
        events.append((tick, mido.Message('note_on', note=pitch, velocity=velocity)))
        events.append((tick + TICKS_PER_BEAT // 2, mido.Message('note_off', note=pitch)))
    midi = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    track = mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=TEMPO, time=0),
                            mido.MetaMessage('time_signature', numerator=4, denominator=4, time=0)])
    midi.tracks.append(track)
    now = 0
    for tick, message in sorted(events, key=lambda event: event[0]):
        track.append(message.copy(time=tick - now))
        now = tick
    midi.save(path)


def four_four(bars=8):
    notes = []
    for bar in range(bars):
        start = bar * 2.0
        notes.append((start, 36, 110))
        for offset, pitch, velocity in ((0, 72, 90), (0.5, 74, 55), (1.0, 76, 60), (1.5, 77, 55)):
            notes.append((start + offset, pitch, velocity))
        for offset, pitch in ((0.25, 79), (0.75, 81), (1.25, 79), (1.75, 81)):
            notes.append((start + offset, pitch, 40))
    return notes


def three_four(bars=10):
    notes = []
    for bar in range(bars):
        start = bar * 1.5
        notes.append((start, 36, 110))
        notes.append((start, 48, 90))
        for offset, pitches, velocity in ((0.25, (79,), 40), (0.5, (43, 55), 65), (0.75, (79,), 40),
                                          (1.0, (41, 53), 68), (1.25, (79,), 40)):
            for pitch in pitches:
                notes.append((start + offset, pitch, velocity))
    return notes


def six_eight(bars=8):
    """A dotted-quarter pulse at 96 BPM; three eighths per beat, accents on beats 1 and 2."""
    eighth = 60.0 / 96 / 3
    notes = []
    for bar in range(bars):
        start = bar * eighth * 6
        for index in range(6):
            strong = index == 0
            notes.append((start + index * eighth, 72 if strong else 74 + index, 90 if strong else 45))
        notes.append((start, 36, 110))
        notes.append((start + 3 * eighth, 43, 80))
    return notes


def flat(bars=8):
    notes = []
    for bar in range(bars):
        for index in range(8):
            notes.append((bar * 2.0 + index * 0.25, 60 + (index * 5) % 12, 80))
    return notes


@unittest.skipUnless(DATA_REPO.is_dir(), 'needs the sibling data repository for temporary files')
class MeterDetection(unittest.TestCase):
    def setUp(self):
        scratch = DATA_REPO / 'scratch'
        scratch.mkdir(exist_ok=True)
        holder = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(holder.cleanup)
        self.path = Path(holder.name) / 'song.mid'

    def detect(self, notes, bpm):
        write_midi(self.path, notes)
        return detect_meter(str(self.path), bpm)

    def test_four_four(self):
        self.assertEqual(self.detect(four_four(), 120)['meter'], (4, 4))

    def test_three_four(self):
        self.assertEqual(self.detect(three_four(), 120)['meter'], (3, 4))

    def test_six_eight_from_a_dotted_quarter_pulse(self):
        detection = self.detect(six_eight(), 96)
        self.assertEqual(detection['meter'], (6, 8))
        self.assertGreater(detection['evidence']['subdivision_3'], detection['evidence']['subdivision_2'])

    def test_flat_accents_are_not_guessed(self):
        detection = self.detect(flat(), 120)
        self.assertIsNone(detection['meter'])
        self.assertEqual(detection['confidence'], 'undetermined')

    def test_too_few_notes_are_not_guessed(self):
        self.assertIsNone(self.detect([(0, 60, 90), (0.5, 62, 90), (1.0, 64, 90)], 120)['meter'])

    def test_detected_meter_reaches_the_bpm_fixed_midi(self):
        write_midi(self.path, three_four())
        output, bpm_info = apply_bpm_fix(None, str(self.path), target_bpm=120, auto_meter=True)
        self.assertEqual(bpm_info['meter'], (3, 4))
        signature = next(message for message in mido.MidiFile(output).tracks[0]
                         if message.type == 'time_signature')
        self.assertEqual((signature.numerator, signature.denominator), (3, 4))

    def test_an_explicit_meter_still_wins(self):
        write_midi(self.path, three_four())
        _, bpm_info = apply_bpm_fix(None, str(self.path), target_bpm=120, meter=(6, 8), auto_meter=True)
        self.assertEqual(bpm_info['meter'], (6, 8))


if __name__ == '__main__':
    unittest.main()
