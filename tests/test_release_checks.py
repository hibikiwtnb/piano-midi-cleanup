"""Regression checks for release-critical input, model, report and hand handling."""

import contextlib
from http.client import IncompleteRead
import io
import queue
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import mido
import postprocess
import hand_split_channels as hand_split
from postprocess_panel import Panel, PanelArgumentParser
from hand_split_channels import collect_notes, group_notes, hand_fits, split_hands_in_place
from transcribe import CHECKPOINT_NAME, ensure_checkpoint


class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def make_midi(self, path, pitches=(47, 54, 59, 61)):
        midi = mido.MidiFile()
        track = mido.MidiTrack()
        midi.tracks.append(track)
        for pitch in pitches:
            track.append(mido.Message('note_on', note=pitch, velocity=80))
        for index, pitch in enumerate(pitches):
            track.append(mido.Message('note_off', note=pitch, time=480 if index == 0 else 0))
        midi.save(path)

    def test_gui_parser_reports_invalid_bpm_without_exiting(self):
        for bpm in ('', 'abc'):
            with self.subTest(bpm=bpm), self.assertRaises(ValueError):
                postprocess.build_parser(parser_class=PanelArgumentParser).parse_args(['song.mid', '--bpm', bpm])
        for bpm in ('0', '-1', 'nan', 'inf', '1e-320', '1e300'):
            args = postprocess.build_parser().parse_args(['song.mid', '--bpm', bpm])
            with self.subTest(bpm=bpm), self.assertRaises(ValueError):
                postprocess.plan_steps(args)

    def test_constrained_split_keeps_feasible_groups_within_limits(self):
        path = self.root / 'song.mid'
        self.make_midi(path)
        split_hands_in_place(path, limits=(4, 12))
        notes = collect_notes(mido.MidiFile(path))
        self.assertEqual(len(notes), 4)
        for group in group_notes(notes):
            for channel in (0, 2):
                pitches = sorted(n['note'] for n in group if n['start_message'].channel == channel)
                self.assertTrue(hand_fits(pitches, 4, 12))

    def test_blob_cost_keeps_three_hand_first_player_below_second_right_above(self):
        blob = (60, 1.0)
        self.assertEqual(hand_split.blob_cost([[48], [55], [72]], blob), 0.0)
        self.assertEqual(hand_split.blob_cost([[48], [65], [72]], blob), 4.0)
        self.assertEqual(hand_split.blob_cost([[48], [55], [55]], blob), 4.0)
        self.assertEqual(hand_split.blob_cost([[65], [55], [72]], blob), 4.0)
        self.assertEqual(hand_split.blob_cost([[48], [55], [72]], (60, 0.5)), 0.0)
        self.assertEqual(hand_split.blob_cost([[48], [65], [72]], (60, 0.5)), 2.0)
        self.assertEqual(hand_split.blob_cost([[48], [55], [72], [84]], blob), 0.0)
        self.assertEqual(hand_split.blob_cost([[48], [55], [55], [84]], blob), 4.0)

    def test_blob_cost_preserves_two_hand_left_right_semantics(self):
        blob = (60, 1.0)
        self.assertEqual(hand_split.blob_cost([[48], [72]], blob), 0.0)
        self.assertEqual(hand_split.blob_cost([[72], [48]], blob), 8.0)

    def test_short_blob_evidence_gap_does_not_handoff_upper_voice(self):
        groups = [[dict(note=p, start_ticks=i * 384, start_seconds=i / 2)
                   for p in ((36, 60, 84) if i in (0, 4) else (84,))] for i in range(5)]
        notes = [note for group in groups for note in group]
        raw = hand_split.blob_lines(notes, 384)
        self.assertEqual(raw[2][1], 0)
        blobs = hand_split.blob_lines(notes, 384, hands=3)
        self.assertEqual([line for line, _ in blobs], [line for line, _ in raw])
        self.assertGreater(blobs[2][1], 0)
        self.assertEqual(hand_split.blob_lines(notes, 384, hands=4), raw)
        hand_split.assign_groups(groups, 3, hand_split.blob_lines(notes, 384, hands=3), 384)
        self.assertEqual(groups[2][0]['hand'], 'second_right')

    def test_blob_confidence_does_not_fill_long_or_unbracketed_solos(self):
        for length, anchored in ((9, True), (5, False)):
            notes = [dict(note=p, start_ticks=i * 384)
                     for i in range(length)
                     for p in ((36, 60, 84) if anchored and i in (0, length - 1) else (84,))]
            raw = hand_split.blob_lines(notes, 384)
            self.assertEqual(raw[length // 2][1], 0)
            self.assertEqual(hand_split.blob_lines(notes, 384, hands=3)[length // 2][1], 0)

    def test_second_player_activation_waits_for_bar_boundary_and_minimum(self):
        off = {'second_player_active': False, 'second_player_start_bar': None, 'second_player_last_bar': None}
        self.assertEqual(hand_split.second_player_modes(off, 3, False, 4),
                         ((False, None, None, 0.0),))
        self.assertEqual(hand_split.second_player_modes(off, 3, True, 4), (
            (False, None, None, 0.0),
            (True, 3, 3, hand_split.SECOND_PLAYER_ACTIVATION_COST + hand_split.SECOND_PLAYER_BAR_COST),
        ))
        on = {'second_player_active': True, 'second_player_start_bar': 3, 'second_player_last_bar': 3}
        self.assertEqual(hand_split.second_player_modes(on, 6, True, 4), ((True, 3, 6, 3.0),))
        self.assertEqual(hand_split.second_player_modes(on, 7, True, 4), (
            (True, 3, 7, 4.0), (False, None, None, 3.0)))

    def test_three_hand_second_player_segments_follow_bar_units(self):
        bar_ticks = 384 * hand_split.BEATS_PER_BAR
        pitches_per_bar = ([(48, 55, 72, 84, 88)] * 2 + [(48, 55)] * 6)
        groups = [[dict(note=pitch, start_ticks=index * bar_ticks, start_seconds=index)
                   for pitch in pitches] for index, pitches in enumerate(pitches_per_bar)]
        blobs = [(60, 1.0)] * (len(groups) * hand_split.BEATS_PER_BAR + 1)
        for minimum_bars, expected in ((4, [True, True, True, True, False, False, False, False]),
                                       (1, [True, True, False, False, False, False, False, False])):
            with self.subTest(minimum_bars=minimum_bars):
                cuts, details = hand_split.search_splits(
                    groups, 3, blobs, 384, second_player_bars=minimum_bars, return_details=True)
                self.assertEqual(details['second_player_active'], expected)
                for group, cut, active in zip(groups, cuts, expected):
                    if not active:
                        self.assertEqual(cut[1], len(group))

    def test_second_player_bars_rejects_invalid_settings(self):
        for value in ('0', '-1', 'nan', 'inf', '1.5'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                postprocess.build_parser(parser_class=PanelArgumentParser).parse_args(
                    ['song.mid', '--set', 'split.second_player_bars=' + value])

    def test_sparse_second_player_rests_when_the_whole_bar_fits_right_chords(self):
        group = [dict(note=p, hand=hand, start_ticks=24 * 384)
                 for p, hand in ((49, 'left'), (64, 'right'), (68, 'right'), (71, 'right'), (75, 'second_right'))]
        self.assertEqual(hand_split.consolidate_sparse_player_bars([group], 384), [7])
        self.assertEqual([n['note'] for n in group if n['hand'] == 'right'], [64, 68, 71, 75])
        self.assertEqual(len(group), 5)

    def test_sparse_bar_decision_is_all_or_nothing_and_keeps_independent_upper_voice(self):
        def group(tick, right, upper):
            return [dict(note=p, hand=hand, start_ticks=tick)
                    for hand, pitches in (('right', right), ('second_right', upper)) for p in pitches]
        for groups in (
                [group(0, (64, 68, 71), (75,)), group(384, (48, 60), (84,))],
                [group(0, (64, 68, 71), (75,)) for _ in range(3)],
                [group(0, (), (84,))],
                [group(0, (64, 68, 71, 72), (75,))]):
            before = [[n['hand'] for n in g] for g in groups]
            self.assertEqual(hand_split.consolidate_sparse_player_bars(groups, 384), [])
            self.assertEqual([[n['hand'] for n in g] for g in groups], before)
        with patch.object(hand_split, 'FIRST_PLAYER_MAX_SPAN', 10):
            self.assertEqual(hand_split.consolidate_sparse_player_bars([group(0, (64, 68, 71), (75,))], 384), [])

    def test_sparse_participation_counts_a_straddling_chord_in_the_new_bar(self):
        boundary = 24 * 384
        preceding = [dict(note=p, hand='second_right', start_ticks=boundary - 384) for p in (83, 87, 90)]
        straddling = [dict(note=p, hand=hand, start_ticks=boundary + offset)
                      for p, hand, offset in ((75, 'second_right', -5), (64, 'right', -2),
                                              (68, 'right', -2), (71, 'right', 1), (49, 'left', 6))]
        later = [dict(note=p, hand=hand, start_ticks=boundary + 576)
                 for p, hand in ((64, 'right'), (68, 'right'), (71, 'right'), (75, 'second_right'))]
        groups = [preceding, straddling, later]
        before = [[n['start_ticks'] for n in group] for group in groups]
        self.assertEqual(hand_split.consolidate_sparse_player_bars(groups, 384), [7])
        self.assertTrue(all(n['hand'] == 'second_right' for n in preceding))
        self.assertFalse(any(n['hand'] == 'second_right' for group in (straddling, later) for n in group))
        self.assertEqual([[n['start_ticks'] for n in group] for group in groups], before)

    def test_second_right_minimum_pitch_accepts_note_names_and_validates_range(self):
        for text, expected in (('C4', 60), ('C5', 72), ('c#5', 73), ('Db5', 73), ('72', 72), ('0', 0), ('127', 127)):
            with self.subTest(text=text):
                args = postprocess.build_parser().parse_args(
                    ['song.mid', '--set', 'split.second_right_min_pitch=' + text])
                self.assertIn(('split.second_right_min_pitch', expected), args.settings)
        for text in ('-1', '128', 'H5', 'C10', '72.5', ''):
            with self.subTest(text=text), self.assertRaises(ValueError):
                postprocess.build_parser(parser_class=PanelArgumentParser).parse_args(
                    ['song.mid', '--set', 'split.second_right_min_pitch=' + text])

    def test_register_preferences_are_symmetric_soft_and_allow_small_excursions(self):
        with patch.object(hand_split, 'SECOND_RIGHT_MIN_PITCH', 72):
            self.assertEqual(hand_split.preferred_register_cost([[48], [71], [72]], True), 0)
            self.assertEqual(hand_split.preferred_register_cost([[48], [72], [84]], True), 10)
            self.assertEqual(hand_split.preferred_register_cost([[48], [90], []], False), 0)
            self.assertEqual(hand_split.preferred_register_cost([[48], [60], [71]], True), 10)
            self.assertGreater(hand_split.preferred_register_cost([[48], [60], [60]], True), 10)
            groups = [[dict(note=p, start_ticks=0, start_seconds=0) for p in (48, 60, 71)]]
            hand_split.assign_groups(groups, 3, [(72, 1)], 384, limits=(1, 0))
            self.assertEqual(groups[0][-1]['hand'], 'second_right')
            self.assertEqual(len(groups[0]), 3)

    def test_melodic_continuity_links_adjacent_pitches_and_releases_after_rest(self):
        def assignments(weight, spacing=384):
            groups = [[dict(note=p, start_ticks=i * spacing, start_seconds=i * spacing / 768)
                       for p in pitches] for i, pitches in enumerate(((60, 67), (55, 61))) ]
            with patch.object(hand_split, 'MELODIC_CONTINUITY_COST', weight):
                blobs = [(59, 0.1)] + [(64, 0.1)] * (spacing // 384)
                hand_split.assign_groups(groups, 2, blobs, 384)
            return [[n['hand'] for n in group] for group in groups]
        baseline = assignments(0)
        self.assertNotEqual(baseline, assignments(4))
        self.assertEqual(assignments(4)[0][0], assignments(4)[1][1])
        self.assertEqual(assignments(4, spacing=768), baseline)

    def test_melodic_links_do_not_join_simultaneous_notes_or_duplicate_pitches(self):
        groups = [[dict(note=p, start_ticks=i * 384) for p in pitches]
                  for i, pitches in enumerate(((48, 60, 84), (49, 61, 83))) ]
        links = hand_split.melodic_links(groups, 384)
        self.assertEqual(links[0], [])
        self.assertEqual([(source, target) for source, target, _ in links[1]],
                         [((0, 0), 0), ((0, 1), 1), ((0, 2), 2)])
        duplicates = [[dict(note=p, start_ticks=i * 384) for p in pitches]
                      for i, pitches in enumerate(((60, 60), (61,))) ]
        self.assertEqual(hand_split.melodic_links(duplicates, 384), [[], []])

    def material_case(self, events):
        return [[dict(note=p, start_ticks=tick, start_seconds=tick / 768) for p in pitches]
                for tick, pitches in events]

    def assign_material_case(self, groups):
        notes = [note for group in groups for note in group]
        with patch.object(hand_split, 'MATERIAL_GROUPING_BARS', 4), \
                patch.object(hand_split, 'SECOND_RIGHT_MIN_PITCH', 72):
            blobs = hand_split.blob_lines(notes, 384, hands=3)
            hand_split.assign_groups(groups, 3, blobs, 384)
        return groups

    def test_material_groups_keep_repeated_four_note_chords_together(self):
        groups = self.material_case([(0, (37, 49, 64, 68, 71, 75)),
                                     (384, (49,)), (576, (49, 64, 68, 71, 75))])
        materials = hand_split.material_groups(groups, 384)
        self.assertIn([64, 68, 71, 75], materials[0]['clusters'])
        self.assign_material_case(groups)
        self.assertTrue(all(n['hand'] == 'right' for g in groups for n in g if n['note'] >= 64))

    def test_material_groups_allocate_the_upper_line_over_several_bars(self):
        events = []
        for bar in range(5):
            base = bar * 1536
            events.extend([(base, (36, 48, 55, 59, 62)), (base + 192, (79,)),
                           (base + 384, (81,)), (base + 576, (55, 59, 62, 83)),
                           (base + 768, (84,)), (base + 960, (81,)),
                           (base + 1152, (48, 55, 59, 62, 79)), (base + 1344, (81,))])
        groups = self.assign_material_case(self.material_case(events))
        self.assertTrue(all(n['hand'] == 'second_right' for g in groups for n in g if n['note'] >= 79))
        self.assertFalse(any(n['hand'] == 'second_right' for g in groups for n in g if n['note'] <= 62))

    def test_material_groups_do_not_require_a_third_hand_for_playable_chords(self):
        groups = self.assign_material_case(self.material_case([(0, (66, 73, 78, 82, 85, 90))]))
        self.assertEqual([n['hand'] for n in groups[0]], ['left'] * 2 + ['right'] * 4)

    def test_material_groups_allow_new_roles_after_a_rest_inside_a_bar(self):
        groups = self.material_case([(0, (68, 75, 78, 83, 87, 90)),
                                     (1500, (47, 54, 59, 61, 66, 71, 73))])
        self.assertEqual(len(hand_split.material_groups(groups, 384)), 2)
        self.assign_material_case(groups)
        self.assertFalse(any(n['hand'] == 'second_right' for g in groups for n in g))

    def test_material_grouping_preserves_impossible_and_duplicate_notes(self):
        groups = self.material_case([(0, (60,) * 14)])
        identities = [id(note) for note in groups[0]]
        self.assign_material_case(groups)
        self.assertEqual([id(note) for note in groups[0]], identities)
        self.assertEqual(len(groups[0]), 14)
        self.assertTrue(all(note['hand'] in hand_split.HANDS[3] for note in groups[0]))
        with patch.object(hand_split, 'MATERIAL_GROUPING_BARS', 4):
            self.assertEqual(hand_split.search_splits([], 3, [], 384), [])

    def test_material_grouping_preserves_activation_envelope_and_hand_order(self):
        groups = self.material_case([(0, (36, 55, 62, 84, 88)),
                                     (1536, (48,)), (3072, (48,)), (4608, (48,)), (6144, (48,))])
        with patch.object(hand_split, 'MATERIAL_GROUPING_BARS', 4):
            cuts, details = hand_split.search_splits(groups, 3, [(72, 1)] * 17, 384,
                                                     second_player_bars=4, return_details=True)
        self.assertEqual(details['second_player_active'], [True, True, True, True, False])
        for group, (low, high) in zip(groups, cuts):
            self.assertTrue(0 <= low <= high <= len(group))

    def test_material_grouping_setting_and_two_hand_compatibility(self):
        for value in (0, 1, 2, 4):
            self.assertEqual(postprocess.parse_setting('split.grouping_bars=' + str(value)),
                             ('split.grouping_bars', value))
        for value in ('-1', '3', '5', 'nan', '1.5'):
            with self.assertRaises(ValueError):
                postprocess.build_parser(parser_class=PanelArgumentParser).parse_args(
                    ['song.mid', '--set', 'split.grouping_bars=' + value])
        groups = self.material_case([(0, (48, 60, 72)), (384, (49, 61, 73))])
        for hands in (2, 4):
            expected = hand_split.search_splits(groups, hands, [(60, 1)] * 2, 384)
            with patch.object(hand_split, 'MATERIAL_GROUPING_BARS', 4):
                self.assertEqual(hand_split.search_splits(groups, hands, [(60, 1)] * 2, 384), expected)

    def test_gui_shows_processed_result_after_success(self):
        source = self.root / 'song.mid'
        self.make_midi(source)
        target = str(self.root / 'song_bpmfix_cleaned.mid')
        for mode, code, changed_selection in (
                ('bpm', 0, False), ('audio', 0, False), ('reuse', 0, False),
                ('reuse', 1, False), ('reuse', 0, True)):
            with self.subTest(mode=mode, code=code, changed_selection=changed_selection):
                panel = object.__new__(Panel)
                panel.running = False
                panel.midi = Mock(get=Mock(return_value=str(source)))
                panel.source = Mock(get=Mock(return_value=mode))
                panel.bpm = Mock(get=Mock(return_value='120'))
                panel.audio = Mock(get=Mock(return_value='input.wav'))
                panel.settings = {}
                panel.preview_path = 'previous.mid'
                panel.current_preset = Mock(return_value={})
                panel.argv = Mock(return_value=[str(source), '--bpm', '120'])
                panel.roll = Mock()
                panel.main_tabs = Mock()
                panel.main_tabs.select.return_value = str(panel.roll)
                panel.run_button = Mock()
                panel.status = Mock()
                panel.root = Mock()
                panel.render_report = Mock()
                panel.report_tree = Mock()
                panel.load_existing_report = Mock(side_effect=lambda **_: panel.refresh_roll())
                panel.lines = queue.Queue()
                with patch('postprocess_panel.save_settings'), patch('postprocess_panel.threading.Thread'):
                    panel.run()
                self.assertEqual(panel.run_preview, (str(source), target))
                if changed_selection:
                    panel.midi.get.return_value = 'other.mid'
                    panel.preview_path = None
                panel.lines.put(('done', code))
                panel.drain()
                if code:
                    self.assertEqual(panel.preview_path, 'previous.mid')
                    panel.roll.show.assert_not_called()
                else:
                    panel.roll.show.assert_called_once_with('other.mid' if changed_selection else target)
                self.assertFalse(panel.running)

    def test_failed_run_preserves_previous_report(self):
        source = self.root / 'song.mid'
        self.make_midi(source)
        report = self.root / 'song_stats.txt'
        original = b'MIDI post-processing run\nOutput: previous.mid\n'
        report.write_bytes(original)
        args = postprocess.build_parser().parse_args([str(source), '--bpm', '120'])
        with patch.object(postprocess, 'apply_bpm_fix', side_effect=RuntimeError('Detection failed')):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                postprocess.run_postprocess(args)
        self.assertEqual(report.read_bytes(), original)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_constrained_split_preserves_unsolved_groups(self):
        path = self.root / 'song.mid'
        self.make_midi(path, pitches=tuple(range(48, 61)))
        split_hands_in_place(path, limits=(4, 12))
        self.assertEqual(len(collect_notes(mido.MidiFile(path))), 13)

    def test_repeated_note_continuity_is_soft_and_expires(self):
        def assignments(weight, spacing=384):
            groups = [
                [dict(note=60, start_ticks=0, start_seconds=0.0),
                 dict(note=67, start_ticks=0, start_seconds=0.0)],
                [dict(note=55, start_ticks=spacing, start_seconds=spacing / 768),
                 dict(note=60, start_ticks=spacing, start_seconds=spacing / 768)],
            ]
            blobs = [(80, 0.1)] * (2 * spacing // 384 + 1)
            with patch.object(hand_split, 'REPEAT_NOTE_COST', weight):
                hand_split.assign_groups(groups, 3, blobs, 384)
            return [[(note['note'], note['hand']) for note in group] for group in groups]

        without_continuity = assignments(0)
        with_continuity = assignments(1)
        self.assertEqual(without_continuity, [
            [(60, 'right'), (67, 'right')],
            [(55, 'left'), (60, 'left')],
        ])
        self.assertEqual(with_continuity, [
            [(60, 'left'), (67, 'right')],
            [(55, 'left'), (60, 'left')],
        ])
        # A rest longer than the context releases the repeated note.
        self.assertEqual(assignments(1, spacing=768), without_continuity)

    def test_repeated_note_continuity_keeps_hard_limits_and_ignores_duplicates(self):
        groups = [[dict(note=pitch, start_ticks=i * 384, start_seconds=i / 2)
                   for pitch in pitches] for i, pitches in enumerate(((60,), (48, 60, 72), (60,)))]
        with patch.object(hand_split, 'REPEAT_NOTE_COST', 100):
            hand_split.assign_groups(groups, 3, [(60, 0.1)] * 3, 384, limits=(1, 12))
            self.assertEqual(len({note['hand'] for note in groups[1]}), 3)
            self.assertEqual(hand_split.repeated_note_cost(
                [[60], [60], []], {60: (2, 0)}, 384, 768, set()), 0)

    def test_repeat_cost_rejects_invalid_settings(self):
        for key in ('split.repeat_cost', 'split.voice_continuity_cost'):
            for value in ('-1', 'nan', 'inf'):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    postprocess.build_parser(parser_class=PanelArgumentParser).parse_args(
                        ['song.mid', '--set', key + '=' + value])

    def test_successful_run_publishes_report_only_on_completion(self):
        source = self.root / 'song_bpmfix.mid'
        self.make_midi(source)
        args = postprocess.build_parser().parse_args([str(source), '--no-trim', '--playable'])
        with contextlib.redirect_stdout(io.StringIO()):
            postprocess.run_postprocess(args)
        report = self.root / 'song_stats.txt'
        self.assertIn('MIDI post-processing run', report.read_text(encoding='utf-8'))
        modified = report.stat().st_mtime_ns
        with contextlib.redirect_stdout(io.StringIO()):
            postprocess.run_postprocess(args)
        self.assertEqual(report.stat().st_mtime_ns, modified)

    def test_model_download_is_atomic_and_does_not_need_wget(self):
        checkpoint = self.root / 'piano_transcription_inference_data' / CHECKPOINT_NAME
        checkpoint.parent.mkdir()
        checkpoint.write_bytes(b'old')
        response = io.BytesIO(b'complete-model')
        response.headers = {'Content-Length': '14'}
        with patch('transcribe.Path.home', return_value=self.root), \
                patch('transcribe.MIN_CHECKPOINT_BYTES', 10), \
                patch('transcribe.urlopen', return_value=response):
            self.assertEqual(ensure_checkpoint(), str(checkpoint))
        self.assertEqual(checkpoint.read_bytes(), b'complete-model')
        with patch('transcribe.Path.home', return_value=self.root), \
                patch('transcribe.MIN_CHECKPOINT_BYTES', 10), patch('transcribe.urlopen') as download:
            ensure_checkpoint()
            download.assert_not_called()

    def test_incomplete_download_preserves_old_checkpoint_and_cleans_temporary(self):
        checkpoint = self.root / 'piano_transcription_inference_data' / CHECKPOINT_NAME
        checkpoint.parent.mkdir()
        checkpoint.write_bytes(b'old')
        for failure in (None, IncompleteRead(b'partial')):
            response = io.BytesIO(b'short')
            response.headers = {'Content-Length': '100'}
            copy_failure = patch('transcribe.shutil.copyfileobj', side_effect=failure) \
                if failure else contextlib.nullcontext()
            with patch('transcribe.Path.home', return_value=self.root), \
                    patch('transcribe.MIN_CHECKPOINT_BYTES', 10), \
                    patch('transcribe.urlopen', return_value=response), copy_failure:
                with self.assertRaises(RuntimeError):
                    ensure_checkpoint()
            self.assertEqual(checkpoint.read_bytes(), b'old')
            self.assertEqual(list(checkpoint.parent.glob('*.download')), [])


if __name__ == '__main__':
    unittest.main()
