import os
import sys
import tempfile
import unittest

import mido

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import key_signature  # noqa: E402

A_MAJOR_BARS = ((57, 61, 64), (62, 66, 69), (64, 68, 71), (57, 61, 64))   # A, D, E, A
BORROWED_BAR = (55, 59, 62)                                               # G (bVII in A)
B_FLAT_BARS = ((58, 62, 65), (63, 67, 70), (65, 69, 72), (58, 62, 65))    # Bb, Eb, F, Bb


def write_bars(bars):
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    for chord in bars:
        for note in chord:
            track.append(mido.Message('note_on', note=note, velocity=80, time=0))
        for index, note in enumerate(chord):
            track.append(mido.Message('note_off', note=note, velocity=0, time=1920 if index == 0 else 0))
    handle = tempfile.NamedTemporaryFile(suffix='.mid', delete=False)
    handle.close()
    midi.save(handle.name)
    return handle.name


class KeySignatureTest(unittest.TestCase):
    def keys(self, bars):
        path = write_bars(bars)
        try:
            return [(segment['bar'], segment['key']) for segment in key_signature.detect_key_segments(path)]
        finally:
            os.remove(path)

    def test_borrowed_chords_keep_home_key(self):
        # A G bar in every other phrase: those bars vote against A, the rest of the song outvotes them.
        bars = []
        for phrase in range(8):
            bars += [A_MAJOR_BARS[0], A_MAJOR_BARS[1], BORROWED_BAR if phrase % 2 else A_MAJOR_BARS[1], A_MAJOR_BARS[2]]
        self.assertEqual(self.keys(bars), [(1, 'A')])

    def test_sustained_section_changes_key(self):
        # A section that returns pays two changes, so it must outlast 2 * CHANGE_COST bars.
        bars = list(A_MAJOR_BARS) * 3 + list(B_FLAT_BARS) * 3 + list(A_MAJOR_BARS) * 3
        self.assertEqual(self.keys(bars), [(1, 'A'), (13, 'Bb'), (25, 'A')])


if __name__ == '__main__':
    unittest.main()
