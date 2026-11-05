"""Glissando correction finds runs on their own scale, leaves crossing chords alone, and repairs only the run."""

from pathlib import Path
import sys
import tempfile
import unittest

import mido

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import gliss
from hand_split_channels import collect_notes

DATA_REPO = Path(__file__).resolve().parents[2] / 'piano-midi-cleanup-data'
TICKS_PER_BEAT = 480
TEMPO = 500000  # 120 BPM: 0.5 s per beat, 0.1 beat = 50 ms
STEP = 0.1
WHITE_DOWN = [93, 91, 89, 88, 86, 84, 83, 81, 79, 77, 76, 74, 72, 71]  # A6 to B4
BLACK_DOWN = [90, 87, 85, 82, 80, 78, 75, 73, 70, 68, 66]  # F#6 to F#4 on black keys


def run_notes(start, pitches, step=STEP, length=1.5, velocity=70):
    return [(start + index * step, pitch, length, velocity) for index, pitch in enumerate(pitches)]


def write_midi(path, notes, key=None):
    """notes: (start beat, pitch, length in beats, velocity)."""
    events = []
    for start, pitch, length, velocity in notes:
        on, off = round(start * TICKS_PER_BEAT), round((start + length) * TICKS_PER_BEAT)
        events.append((on, 1, mido.Message('note_on', note=pitch, velocity=velocity)))
        events.append((off, 0, mido.Message('note_off', note=pitch)))
    midi = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    track = mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=TEMPO, time=0)])
    if key:
        track.append(mido.MetaMessage('key_signature', key=key, time=0))
    midi.tracks.append(track)
    now = 0
    for tick, _, message in sorted(events, key=lambda event: event[:2]):
        track.append(message.copy(time=tick - now))
        now = tick
    midi.save(path)


def detect(path, notes, key=None):
    write_midi(path, notes, key)
    return gliss.detect_runs(mido.MidiFile(path))


def member_pitches(run):
    return [note['note'] for note in run['members']]


class GlissTest(unittest.TestCase):
    def setUp(self):
        DATA_REPO.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=DATA_REPO)
        self.path = str(Path(self.folder.name) / 'song.mid')

    def tearDown(self):
        self.folder.cleanup()


class DetectionTest(GlissTest):
    def test_white_key_run_down(self):
        runs, _ = detect(self.path, run_notes(0, WHITE_DOWN))
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['scale_name'], 'white')
        self.assertEqual(runs[0]['direction'], -1)
        self.assertEqual(member_pitches(runs[0]), WHITE_DOWN)

    def test_white_key_run_up(self):
        runs, _ = detect(self.path, run_notes(0, list(reversed(WHITE_DOWN))))
        self.assertEqual([run['direction'] for run in runs], [1])

    def test_black_key_run(self):
        runs, _ = detect(self.path, run_notes(0, BLACK_DOWN))
        self.assertEqual([run['scale_name'] for run in runs], ['black'])

    def test_major_scale_run_with_its_own_notes(self):
        e_major = [88, 87, 85, 83, 81, 80, 78, 76, 75, 73, 71]
        runs, _ = detect(self.path, run_notes(0, e_major))
        self.assertEqual([run['scale_name'] for run in runs], ['E major'])

    def test_ambiguous_scale_uses_the_key_signature(self):
        # A G major or D major run without F# or C# would pass as white; B-flat with no E-flat fits F and B-flat major.
        run = [81, 79, 77, 74, 72, 70, 69, 67, 65, 62]
        runs, _ = detect(self.path, run_notes(0, run), key='F')
        self.assertEqual([item['scale_name'] for item in runs], ['F major'])

    def test_ambiguous_scale_without_a_deciding_key(self):
        run = [81, 79, 77, 74, 72, 70, 69, 67, 65, 62]
        runs, _ = detect(self.path, run_notes(0, run), key='E')
        self.assertEqual(len(runs), 1)
        self.assertIsNone(runs[0]['scale'])
        self.assertTrue(runs[0]['scale_name'].startswith('ambiguous'))

    def test_out_of_order_notes_are_still_one_run(self):
        shuffled = WHITE_DOWN[:8] + [76, 77, 72, 74, 71]
        runs, _ = detect(self.path, run_notes(0, shuffled))
        self.assertEqual(len(runs), 1)
        self.assertEqual(sorted(member_pitches(runs[0])), sorted(shuffled))

    def test_played_scale_is_too_slow(self):
        runs, _ = detect(self.path, run_notes(0, WHITE_DOWN, step=0.25))
        self.assertEqual(runs, [])

    def test_low_octave_chords_are_not_a_run(self):
        low = [55, 59, 60, 62, 64, 65, 67, 69]
        runs, _ = detect(self.path, run_notes(0, low))
        self.assertEqual(runs, [])

    def test_a_short_figure_is_not_a_run(self):
        runs, _ = detect(self.path, run_notes(0, WHITE_DOWN[:5]))
        self.assertEqual(runs, [])

    def test_a_note_past_a_gap_is_not_part_of_the_run(self):
        late = (WHITE_DOWN[-1] - 3, )
        notes = run_notes(0, WHITE_DOWN) + [(len(WHITE_DOWN) * STEP + 0.3, late[0], 1, 70)]
        runs, _ = detect(self.path, notes)
        self.assertEqual(member_pitches(runs[0]), WHITE_DOWN)

    def test_two_notes_past_a_gap_are_not_part_of_the_run(self):
        at = len(WHITE_DOWN) * STEP + 0.3
        notes = run_notes(0, WHITE_DOWN) + [(at, 67, 1, 70), (at + 0.01, 65, 1, 70)]
        runs, _ = detect(self.path, notes)
        self.assertEqual(member_pitches(runs[0]), WHITE_DOWN)

    def test_a_missed_note_inside_the_run_does_not_cut_it(self):
        pitches = [pitch for pitch in WHITE_DOWN if pitch not in (86, 84, 83)]
        notes = [(index * STEP, pitch, 1, 70) for index, pitch in enumerate(WHITE_DOWN) if pitch in pitches]
        runs, _ = detect(self.path, notes)
        self.assertEqual(member_pitches(runs[0]), pitches)

    def test_late_notes_near_the_end_claim_empty_degrees(self):
        # The onsets of Dream Believers m136, in beats: the end of a descent comes out of order and late.
        head = [1.19, 1.22, 1.28, 1.32, 1.39, 1.46, 1.53, 1.64, 1.72, 1.81, 1.86, 1.96, 2.08, 2.14]
        tail = [(2.40, 64), (2.47, 67), (2.53, 60), (2.64, 65), (2.71, 62), (2.73, 59)]
        notes = [(at, pitch, 1, 70) for at, pitch in list(zip(head, WHITE_DOWN)) + tail]
        runs, _ = detect(self.path, notes)
        self.assertEqual(sorted(member_pitches(runs[0])), sorted(WHITE_DOWN + [pitch for _, pitch in tail]))


class ThroughNoteTest(GlissTest):
    def test_chord_off_the_line_stays_out(self):
        chord = [(0.5, 60, 1, 80), (0.5, 63, 1, 80), (0.5, 68, 1, 80)]
        runs, _ = detect(self.path, run_notes(0, WHITE_DOWN) + chord)
        self.assertEqual(member_pitches(runs[0]), WHITE_DOWN)

    def test_note_struck_before_the_run_reaches_it_stays_out(self):
        # The hand-corrected first song keeps E5 of m9 in a chord, about 5 degrees before the run reaches it.
        holes = [pitch for pitch in WHITE_DOWN if pitch != 76]
        notes = [(WHITE_DOWN.index(pitch) * STEP, pitch, 1, 70) for pitch in holes] + [(5 * STEP, 76, 1, 80)]
        runs, _ = detect(self.path, notes)
        self.assertNotIn(76, member_pitches(runs[0]))

    def test_note_held_from_before_stays_out(self):
        runs, _ = detect(self.path, [(-0.0, 80, 4, 80)] + run_notes(1, WHITE_DOWN))
        self.assertNotIn(80, member_pitches(runs[0]))

    def test_chord_note_at_the_end_of_the_run_goes_to_the_chord(self):
        pitches = WHITE_DOWN + [69]
        end = (len(pitches) - 1) * STEP
        chord = [(end, 45, 1, 80), (end, 52, 1, 80)]
        runs, _ = detect(self.path, run_notes(0, pitches) + chord)
        self.assertNotIn(69, member_pitches(runs[0]))

    def test_chord_in_the_middle_of_the_run_keeps_the_run_whole(self):
        # The hand-corrected first song keeps run notes that strike together with a chord.
        at = 5 * STEP
        chord = [(at, 45, 1, 80), (at, 52, 1, 80)]
        runs, _ = detect(self.path, run_notes(0, WHITE_DOWN) + chord)
        self.assertEqual(member_pitches(runs[0]), WHITE_DOWN)

    def test_repeated_chord_at_the_end_goes_to_the_chord(self):
        pitches = WHITE_DOWN + [69]
        end = (len(pitches) - 1) * STEP
        struck = [(end, 52, 1, 80)]
        repeat = [(end + 1, 69, 1, 80), (end + 1, 52, 1, 80)]
        runs, _ = detect(self.path, run_notes(0, pitches) + struck + repeat)
        self.assertNotIn(69, member_pitches(runs[0]))

    def test_repeated_chord_in_the_middle_keeps_the_note(self):
        at = 5 * STEP
        pitch = WHITE_DOWN[5]
        notes = run_notes(0, WHITE_DOWN) + [(at, 52, 1, 80), (at + 1, pitch, 1, 80), (at + 1, 52, 1, 80)]
        runs, _ = detect(self.path, notes)
        self.assertIn(pitch, member_pitches(runs[0]))

    def test_mostly_repeated_chords_are_rejected(self):
        pitches = WHITE_DOWN[:10]
        notes = run_notes(0, pitches)
        for index, pitch in enumerate(pitches[:6], 1):
            at = index * STEP
            pitch = pitches[index]
            notes += [(at, 52, 0.5, 80), (at + 1.2, pitch, 0.5, 80), (at + 1.2, 52, 0.5, 80)]
        runs, rejected = detect(self.path, notes)
        self.assertEqual(runs, [])
        self.assertEqual(len(rejected), 1)


class RepairTest(GlissTest):
    def restored(self, notes, fill=False):
        write_midi(self.path, notes)
        output = str(Path(self.folder.name) / 'out.mid')
        runs, _ = gliss.extract_runs(self.path, output)
        rows = gliss.restore_runs(output, runs, fill=fill)
        return collect_notes(mido.MidiFile(output)), rows

    def test_extract_leaves_only_other_notes_and_the_input_untouched(self):
        chord = [(0.5, 60, 1, 80), (0.5, 63, 1, 80)]
        write_midi(self.path, run_notes(0, WHITE_DOWN) + chord)
        before = Path(self.path).read_bytes()
        output = str(Path(self.folder.name) / 'out.mid')
        gliss.extract_runs(self.path, output)
        self.assertEqual(sorted(note['note'] for note in collect_notes(mido.MidiFile(output))), [60, 63])
        self.assertEqual(Path(self.path).read_bytes(), before)

    def test_nothing_detected_writes_the_same_notes(self):
        notes = [(0, 60, 1, 80), (1, 64, 1, 80)]
        restored, rows = self.restored(notes)
        self.assertEqual(rows, [])
        self.assertEqual([(note['start_ticks'], note['note']) for note in restored], [(0, 60), (480, 64)])

    def test_every_note_gets_one_length(self):
        restored, rows = self.restored(run_notes(0, WHITE_DOWN, length=1.5))
        lengths = {note['end_ticks'] - note['start_ticks'] for note in restored}
        self.assertEqual(lengths, {round(STEP * 1.25 * TICKS_PER_BEAT)})
        self.assertEqual(len(rows), 1)

    def test_length_stays_within_a_sixteenth(self):
        slow = [(index * 0.11, pitch, 2, 70) for index, pitch in enumerate(WHITE_DOWN)]
        restored, _ = self.restored(slow)
        self.assertLessEqual(max(note['end_ticks'] - note['start_ticks'] for note in restored), TICKS_PER_BEAT / 4)

    def test_out_of_order_pitches_are_sorted_on_their_onsets(self):
        shuffled = WHITE_DOWN[:8] + [76, 77, 72, 74, 71]
        restored, _ = self.restored(run_notes(0, shuffled))
        self.assertEqual([note['note'] for note in restored], sorted(shuffled, reverse=True))
        self.assertEqual([note['start_ticks'] for note in restored],
                         [round(index * STEP * TICKS_PER_BEAT) for index in range(len(shuffled))])

    def test_fill_off_adds_nothing(self):
        holes = [pitch for pitch in WHITE_DOWN if pitch not in (84, 77)]
        restored, _ = self.restored(run_notes(0, holes))
        self.assertEqual(sorted(note['note'] for note in restored), sorted(holes))

    def test_fill_adds_white_keys_on_a_white_run(self):
        holes = [pitch for pitch in WHITE_DOWN if pitch not in (84, 77)]
        restored, rows = self.restored(run_notes(0, holes), fill=True)
        self.assertEqual([note['note'] for note in restored], WHITE_DOWN)
        self.assertEqual(rows[0]['filled'], [84, 77])

    def test_fill_adds_black_keys_on_a_black_run(self):
        holes = [pitch for pitch in BLACK_DOWN if pitch != 80]
        restored, _ = self.restored(run_notes(0, holes), fill=True)
        self.assertEqual([note['note'] for note in restored], BLACK_DOWN)

    def test_fill_adds_the_scale_notes_of_a_major_scale_run(self):
        e_major = [88, 87, 85, 83, 81, 80, 78, 76, 75, 73, 71]
        holes = [pitch for pitch in e_major if pitch != 80]
        restored, _ = self.restored(run_notes(0, holes), fill=True)
        self.assertEqual([note['note'] for note in restored], e_major)

    def test_no_fill_on_an_ambiguous_scale(self):
        run = [81, 79, 74, 72, 70, 69, 67, 65, 62]
        restored, rows = self.restored(run_notes(0, run), fill=True)
        self.assertEqual(rows[0]['filled'], [])
        self.assertEqual(len(restored), len(run))

    def test_fill_takes_the_midpoint_and_mean_velocity(self):
        pitches = [93, 91, 89, 88, 86, 84, 81, 79, 77, 76, 74, 72, 71]  # B5 (83) missing
        notes = [(index * STEP, pitch, 1, 60 if pitch == 84 else 80) for index, pitch in enumerate(pitches)]
        restored, _ = self.restored(notes, fill=True)
        added = [note for note in restored if note['note'] == 83][0]
        self.assertEqual(added['start_ticks'], round(5.5 * STEP * TICKS_PER_BEAT))
        self.assertEqual(added['velocity'], 70)

    def test_no_fill_on_a_key_that_is_held(self):
        holes = [pitch for pitch in WHITE_DOWN if pitch != 84]
        write_midi(self.path, run_notes(0, holes))
        output = str(Path(self.folder.name) / 'out.mid')
        runs, _ = gliss.extract_runs(self.path, output)
        held = mido.MidiFile(output)
        held.tracks[0].insert(1, mido.Message('note_on', note=84, velocity=80, time=0))
        held.tracks[0].insert(2, mido.Message('note_off', note=84, time=960))
        held.save(output)
        rows = gliss.restore_runs(output, runs, fill=True)
        self.assertEqual(rows[0]['held'], [84])
        self.assertEqual(rows[0]['filled'], [])

    def test_run_note_stops_before_the_same_key_is_struck_again(self):
        notes = run_notes(0, WHITE_DOWN) + [(0.05, 93, 1, 80)]
        write_midi(self.path, notes)
        output = str(Path(self.folder.name) / 'out.mid')
        runs, _ = gliss.extract_runs(self.path, output)
        rows = gliss.restore_runs(output, runs)
        result = collect_notes(mido.MidiFile(output))
        top = sorted((note for note in result if note['note'] == 93), key=lambda note: note['start_ticks'])
        self.assertEqual(top[1]['end_ticks'] - top[1]['start_ticks'], 480)  # the other note keeps its length
        self.assertLessEqual(top[0]['end_ticks'], top[1]['start_ticks'])
        self.assertEqual(rows[0]['dropped'], 0)

    def test_hand_follows_the_starting_pitch(self):
        write_midi(self.path, run_notes(0, WHITE_DOWN) + [(0.5, 50, 1, 80)])
        output = str(Path(self.folder.name) / 'out.mid')
        runs, _ = gliss.extract_runs(self.path, output)
        gliss.assign_hands(runs, output)
        self.assertEqual(runs[0]['hand'], 'right')

    def test_upward_run_under_a_melody_goes_to_the_left_hand(self):
        up = list(reversed(WHITE_DOWN))
        write_midi(self.path, run_notes(0, up) + [(0.5, 96, 1, 80), (0.9, 98, 1, 80)])
        output = str(Path(self.folder.name) / 'out.mid')
        runs, _ = gliss.extract_runs(self.path, output)
        gliss.assign_hands(runs, output)
        self.assertEqual(runs[0]['hand'], 'left')

    def test_no_other_notes_use_the_middle_c_boundary(self):
        write_midi(self.path, run_notes(0, WHITE_DOWN))
        output = str(Path(self.folder.name) / 'out.mid')
        runs, _ = gliss.extract_runs(self.path, output)
        gliss.assign_hands(runs, output)
        self.assertEqual(runs[0]['hand'], 'right')


if __name__ == '__main__':
    unittest.main()
