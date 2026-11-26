"""The measure review reports suspected places and never edits the MIDI or MusicXML."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hand_split_channels import CHANNELS
import measure_quality

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
    track = mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=500000, time=0),
                            mido.MetaMessage('time_signature', numerator=4, denominator=4, time=0)])
    midi.tracks.append(track)
    now = 0
    for tick, _, message in sorted(events, key=lambda event: event[:2]):
        track.append(message.copy(time=tick - now))
        now = tick
    midi.save(path)


def note_element(step, octave, duration, voice='1', staff='1', note_type='quarter',
                 beams=(), ties=()):
    beam_xml = ''.join('<beam number="{}">{}</beam>'.format(number, kind) for number, kind in beams)
    tie_xml = ''.join('<tie type="{}"/>'.format(kind) for kind in ties)
    tied_xml = ''.join('<tied type="{}"/>'.format(kind) for kind in ties)
    notations = '<notations>{}</notations>'.format(tied_xml) if tied_xml else ''
    return ('<note><pitch><step>{}</step><octave>{}</octave></pitch><duration>{}</duration>'
            '<voice>{}</voice><type>{}</type><staff>{}</staff>{}{}{}</note>').format(
                step, octave, duration, voice, note_type, staff, beam_xml, tie_xml, notations)


def rest_element(duration, voice='1', staff='1', note_type='16th'):
    return ('<note><rest/><duration>{}</duration><voice>{}</voice><type>{}</type>'
            '<staff>{}</staff></note>').format(duration, voice, note_type, staff)


def write_musicxml(path, measure_bodies):
    measures = ''.join(
        '<measure number="{}">{}</measure>'.format(index, body)
        for index, body in enumerate(measure_bodies, 1)
    )
    text = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Piano</part-name>'
            '</score-part></part-list><part id="P1">'
            '{}</part></score-partwise>').format(measures)
    Path(path).write_text(text, encoding='utf-8')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def codes(result, measure):
    for entry in result['measures']:
        if entry['display_measure'] == measure:
            return {issue['code'] for issue in entry['issues']}
    return set()


class MeasureQualityTest(unittest.TestCase):
    def setUp(self):
        DATA_REPO.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=DATA_REPO)
        self.midi = str(Path(self.folder.name) / 'song.mid')
        self.xml = str(Path(self.folder.name) / 'song.musicxml')

    def tearDown(self):
        self.folder.cleanup()

    def analyze(self, notes, xml=None, **options):
        write_midi(self.midi, notes)
        if xml is not None:
            write_musicxml(self.xml, xml)
        return measure_quality.analyze(self.midi, self.xml if xml is not None else None, **options)

    def test_an_unplayable_onset_group_is_located_by_midi_event(self):
        result = self.analyze([(0, pitch, 1) for pitch in range(60, 70)])
        measure = next(entry for entry in result['measures'] if entry['display_measure'] == 1)
        issue = next(issue for issue in measure['issues'] if issue['code'] == 'unplayable_onset_group')
        self.assertEqual(issue['suggested_review'], '演奏困難')
        self.assertEqual(len(issue['notes']), 10)
        self.assertEqual(issue['notes'][0]['track'], 0)
        self.assertIn('start_tick', issue['notes'][0])
        self.assertIn('channel', issue['notes'][0])

    def test_a_gapped_four_note_chord_is_flagged_but_a_close_chord_is_not(self):
        gapped = self.analyze([(0, pitch, 1) for pitch in (60, 64, 71, 75)])
        close = self.analyze([(0, pitch, 1) for pitch in (60, 64, 67, 72)])
        self.assertIn('unusual_chord_spacing', codes(gapped, 1))
        self.assertNotIn('unusual_chord_spacing', codes(close, 1))

    def test_a_missing_octave_partner_inside_an_octave_run_is_suspected(self):
        notes = [(4, 67, 0.25), (4, 79, 0.25),
                 (4.5, 78, 0.25),
                 (5, 67, 0.25), (5, 79, 0.25),
                 (5.5, 69, 0.25), (5.5, 81, 0.25)]
        result = self.analyze(notes)
        measure = next(entry for entry in result['measures'] if entry['display_measure'] == 2)
        issue = next(issue for issue in measure['issues'] if issue['code'] == 'suspected_missing_octave_note')
        self.assertEqual(issue['missing_partner']['pitch'], 66)
        self.assertEqual(issue['suggested_review'], '疑似漏音')

    def test_a_complete_octave_run_is_not_reported_as_missing_a_note(self):
        notes = []
        for index, top in enumerate((79, 77, 79, 81)):
            notes.extend([(index, top - 12, 0.5), (index, top, 0.5)])
        result = self.analyze(notes)
        self.assertFalse(any('suspected_missing_octave_note' in codes(result, measure)
                             for measure in (1,)))

    def test_one_shifted_note_in_a_repeated_chord_is_reported_with_the_offset(self):
        notes = ([(4, pitch, 0.5) for pitch in (60, 64, 67)]
                 + [(5, pitch, 0.5) for pitch in (60, 64, 68)]
                 + [(6, pitch, 0.5) for pitch in (60, 64, 67)])
        result = self.analyze(notes)
        measure = next(entry for entry in result['measures'] if entry['display_measure'] == 2)
        issue = next(issue for issue in measure['issues'] if issue['code'] == 'single_note_semitone_shift')
        self.assertEqual(issue['offset_semitones'], 1)
        self.assertTrue(issue['returns'])

    def test_fast_octave_double_stops_are_reported_as_load(self):
        notes = []
        for index, top in enumerate((79, 81, 83)):
            notes.extend([(index * 0.25, top - 12, 0.2), (index * 0.25, top, 0.2)])
        result = self.analyze(notes)
        self.assertIn('fast_parallel_double_stops', codes(result, 1))

    def test_pedal_like_sustained_overlap_is_not_called_unplayable(self):
        notes = [(0, 60, 4), (1, 62, 3), (2, 64, 2)]
        result = self.analyze(notes)
        self.assertNotIn('unplayable_onset_group', codes(result, 1))
        measure = next(entry for entry in result['measures'] if entry['display_measure'] == 1)
        self.assertIn('sustained_overlap', {issue['code'] for issue in measure['issues']})

    def test_bar_offset_only_changes_the_displayed_measure(self):
        result = self.analyze([(4, pitch, 1) for pitch in (60, 64, 71, 75)], bar_offset=-2)
        self.assertIn('unusual_chord_spacing', codes(result, 0))
        self.assertEqual(result['priority'][0]['measure'], 2)

    def test_repeated_analysis_is_identical_and_writes_nothing(self):
        notes = [(0, pitch, 1) for pitch in (60, 64, 71, 75)]
        xml = ['<attributes><divisions>4</divisions></attributes>'
               + note_element('C', 4, 4)]
        first = self.analyze(notes, xml)
        before_midi = sha256(self.midi)
        before_xml = sha256(self.xml)
        second = measure_quality.analyze(self.midi, self.xml)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual(sha256(self.midi), before_midi)
        self.assertEqual(sha256(self.xml), before_xml)

    def test_an_isolated_extra_voice_is_reported(self):
        body = ('<attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type>'
                '</time></attributes>'
                + note_element('C', 4, 1) + note_element('D', 4, 1) + note_element('E', 4, 1)
                + note_element('F', 4, 1)
                + note_element('G', 4, 1, voice='2'))
        result = self.analyze([(0, 60, 1)], [body])
        self.assertIn('isolated_extra_voice', codes(result, 1))

    def test_an_unnecessary_beam_break_is_reported(self):
        body = ('<attributes><divisions>4</divisions></attributes>'
                + note_element('C', 4, 2, note_type='eighth', beams=(('1', 'end'),))
                + note_element('D', 4, 2, note_type='eighth', beams=(('1', 'begin'),)))
        result = self.analyze([(0, 60, 1)], [body])
        self.assertIn('unnecessary_beam_break', codes(result, 1))

    def test_a_tie_chain_inside_one_measure_is_reported(self):
        body = ('<attributes><divisions>4</divisions></attributes>'
                + note_element('C', 4, 2, ties=('start',))
                + note_element('C', 4, 2, ties=('stop', 'start'))
                + note_element('C', 4, 2, ties=('stop',)))
        result = self.analyze([(0, 60, 1.5)], [body])
        self.assertIn('unnecessary_tie_chain', codes(result, 1))

    def test_a_short_rest_inside_a_phrase_is_reported(self):
        body = ('<attributes><divisions>4</divisions></attributes>'
                + note_element('C', 4, 2, note_type='eighth')
                + rest_element(1)
                + note_element('D', 4, 2, note_type='eighth'))
        result = self.analyze([(0, 60, 1)], [body])
        self.assertIn('short_rest_fragmentation', codes(result, 1))

    def test_legitimate_cross_bar_style_tie_and_full_second_voice_are_not_flagged(self):
        bodies = []
        for index in range(6):
            parts = '<attributes><divisions>4</divisions></attributes>'
            parts += note_element('C', 4, 1, ties=('start',) if index == 0 else ('stop', 'start'))
            parts += ''.join(note_element(step, 4, 1, voice='2') for step in ('E', 'F', 'G', 'A'))
            bodies.append(parts)
        result = self.analyze([(beat, 60, 1) for beat in range(0, 24, 4)], bodies)
        self.assertNotIn('unnecessary_tie_chain', codes(result, 1))
        self.assertNotIn('isolated_extra_voice', codes(result, 2))

    def test_plain_midi_marks_notation_as_not_evaluated(self):
        result = self.analyze([(0, 60, 1)])
        measure = result['measures'][0]
        self.assertIsNone(measure['scores']['notation'])
        self.assertFalse(result['dimensions']['notation'])


if __name__ == '__main__':
    unittest.main()
