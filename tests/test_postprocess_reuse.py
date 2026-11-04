"""Reusing the tempo of a MIDI corrected by hand: it becomes song_bpmfix.mid without being changed."""

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import postprocess

DATA_REPO = Path(__file__).resolve().parents[2] / 'piano-midi-cleanup-data'
NO_STEPS = ['--no-clean', '--no-split', '--no-trim']


def write_midi(path, pitches=(60, 64, 67), tempo=461538):
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=tempo, time=0)])
    for pitch in pitches:
        track.append(mido.Message('note_on', note=pitch, velocity=80, time=0))
        track.append(mido.Message('note_off', note=pitch, velocity=0, time=480))
    midi.tracks.append(track)
    midi.save(str(path))


def run(*argv):
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = postprocess.main([str(arg) for arg in argv])
    return code, output.getvalue()


def note_starts(path):
    midi = mido.MidiFile(str(path))
    ticks, starts = 0, []
    for message in midi.tracks[0]:
        ticks += message.time
        if message.type == 'note_on' and message.velocity > 0:
            starts.append((ticks, message.note))
    return starts, midi.ticks_per_beat, [m.tempo for m in midi.tracks[0] if m.type == 'set_tempo']


@unittest.skipUnless(DATA_REPO.is_dir(), 'needs the sibling data repository for temporary files')
class ReuseHandCorrectedMidi(unittest.TestCase):
    def setUp(self):
        scratch = DATA_REPO / 'scratch'
        scratch.mkdir(exist_ok=True)
        holder = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name)
        self.song = self.folder / 'song_Edited.mid'
        self.bpmfix = self.folder / 'song_Edited_bpmfix.mid'
        self.cleaned = self.folder / 'song_Edited_bpmfix_cleaned.mid'

    def test_input_without_bpmfix_becomes_it_unchanged(self):
        write_midi(self.song)
        original = self.song.read_bytes()
        code, _ = run(self.song, *NO_STEPS)
        self.assertEqual(code, 0)
        self.assertEqual(self.song.read_bytes(), original)
        self.assertEqual(self.bpmfix.read_bytes(), original)
        self.assertEqual(note_starts(self.cleaned), note_starts(self.song))
        report = (self.folder / 'song_Edited_stats.txt').read_text(encoding='utf-8')
        self.assertIn('Use this MIDI as the BPM-fixed MIDI, as it is', report)

    def test_existing_bpmfix_is_kept(self):
        write_midi(self.song, pitches=(60,))
        write_midi(self.bpmfix, pitches=(72,))
        kept = self.bpmfix.read_bytes()
        os.utime(self.song, (1000, 1000))
        os.utime(self.bpmfix, (2000, 2000))
        code, output = run(self.song, *NO_STEPS)
        self.assertEqual(code, 0)
        self.assertEqual(self.bpmfix.read_bytes(), kept)
        self.assertEqual(note_starts(self.cleaned)[0], [(0, 72)])
        self.assertNotIn('Warning', output)

    def test_input_newer_than_bpmfix_warns_and_still_uses_bpmfix(self):
        write_midi(self.song, pitches=(60,))
        write_midi(self.bpmfix, pitches=(72,))
        os.utime(self.bpmfix, (1000, 1000))
        os.utime(self.song, (2000, 2000))
        code, output = run(self.song, *NO_STEPS)
        self.assertEqual(code, 0)
        self.assertIn('is newer than', output)
        self.assertEqual(note_starts(self.cleaned)[0], [(0, 72)])

    def test_cleaned_input_without_bpmfix_still_fails(self):
        write_midi(self.cleaned)
        code, output = run(self.cleaned, *NO_STEPS)
        self.assertEqual(code, 1)
        self.assertIn('No BPM-fixed MIDI to reuse', output)
        self.assertFalse(self.bpmfix.exists())


if __name__ == '__main__':
    unittest.main()
