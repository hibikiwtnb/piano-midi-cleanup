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
from postprocess_panel import Panel, PanelArgumentParser, default_preset, normalize_preset
from hand_split_channels import collect_notes, group_notes, hand_fits, split_hands_in_place
from note_changes import load_changes
from transcribe import CHECKPOINT_NAME, ensure_checkpoint


class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def note_group(self, pitches, tick=0, seconds=0.0):
        return [dict(note=pitch, start_ticks=tick, start_seconds=seconds, velocity=80)
                for pitch in pitches]

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
        def assignments(weight, clarity, spacing=384):
            groups = [[dict(note=60, start_ticks=tick, start_seconds=tick / 768)]
                      for tick in (0, spacing, 2 * spacing)]
            blobs = [(61, clarity)] * (2 * spacing // 384 + 1)
            blobs[spacing // 384] = (60, clarity)
            with patch.object(hand_split, 'REPEAT_NOTE_COST', weight):
                hand_split.assign_groups(groups, 3, blobs, 384)
            return [group[0]['hand'] for group in groups]

        self.assertEqual(assignments(0, 0.1), ['left', 'right', 'left'])
        self.assertEqual(assignments(1, 0.1), ['left', 'left', 'left'])
        # Strong contrary evidence still permits a handoff; rests clear the preference.
        self.assertEqual(assignments(1, 1), ['left', 'right', 'left'])
        self.assertEqual(assignments(1, 0.1, spacing=1152), ['left', 'right', 'left'])

    def test_repeated_note_continuity_keeps_hard_limits_and_ignores_duplicates(self):
        groups = [[dict(note=pitch, start_ticks=i * 384, start_seconds=i / 2)
                   for pitch in pitches] for i, pitches in enumerate(((60,), (48, 60, 72), (60,)))]
        with patch.object(hand_split, 'REPEAT_NOTE_COST', 100):
            hand_split.assign_groups(groups, 3, [(60, 0.1)] * 3, 384, limits=(1, 12))
            self.assertEqual(len({note['hand'] for note in groups[1]}), 3)
            self.assertEqual(hand_split.repeated_note_cost(
                [[60], [60], []], {60: (2, 0)}, 384, 768, set()), 0)

    def test_repeat_cost_rejects_invalid_settings(self):
        for value in ('-1', 'nan', 'inf'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                postprocess.build_parser(parser_class=PanelArgumentParser).parse_args(
                    ['song.mid', '--set', 'split.repeat_cost=' + value])

    def test_primo_activation_bars_cli_rejects_invalid_values(self):
        parser = postprocess.build_parser(parser_class=PanelArgumentParser)
        self.assertEqual(parser.parse_args(['song.mid']).primo_activation_bars, 4)
        self.assertEqual(
            parser.parse_args(['song.mid', '--hands', '3', '--primo-activation-bars', '1']).primo_activation_bars, 1)
        for value in ('0', '-1', 'abc', '1.5'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parser.parse_args(['song.mid', '--primo-activation-bars', value])

    def test_primo_transitions_wait_for_bar_boundary_and_minimum(self):
        off = {'primo_active': False, 'primo_start_bar': None, 'primo_last_bar': None}
        self.assertEqual(hand_split.primo_active_modes(off, 3, False, 4),
                         ((False, None, None, 0.0),))
        self.assertEqual(hand_split.primo_active_modes(off, 3, True, 4),
                         ((False, None, None, 0.0), (True, 3, 3, 9.0)))
        on = {'primo_active': True, 'primo_start_bar': 3, 'primo_last_bar': 3}
        before_minimum = hand_split.primo_active_modes(on, 6, True, 4)
        self.assertEqual([mode[0] for mode in before_minimum], [True])
        self.assertEqual(before_minimum[0][3], 3.0)
        self.assertEqual(hand_split.primo_active_modes(on, 7, False, 4),
                         ((True, 3, 3, 0.0),))
        self.assertIn((False, None, None, 3.0), hand_split.primo_active_modes(on, 7, True, 4))

    def test_primo_bar_segments_include_silent_bars(self):
        chain = [
            {'primo_active': False, 'primo_start_bar': None},
            {'primo_active': True, 'primo_start_bar': 0},
            {'primo_active': True, 'primo_start_bar': 0},
            {'primo_active': False, 'primo_start_bar': None},
            {'primo_active': True, 'primo_start_bar': 8},
            {'primo_active': True, 'primo_start_bar': 8},
        ]
        self.assertEqual(hand_split.primo_bar_segments(chain, [0, 0, 4, 5, 8, 10]),
                         [(1, 5), (9, 11)])

    def test_primo_setting_does_not_change_two_or_four_hands(self):
        def assignments(hands, primo_activation_bars):
            groups = [
                [dict(note=48, start_ticks=0, start_seconds=0.0),
                 dict(note=55, start_ticks=0, start_seconds=0.0)],
                [dict(note=60, start_ticks=384, start_seconds=0.5)],
                [dict(note=55, start_ticks=1536, start_seconds=2.0),
                 dict(note=64, start_ticks=1536, start_seconds=2.0),
                 dict(note=84, start_ticks=1536, start_seconds=2.0)],
                [dict(note=50, start_ticks=3072, start_seconds=4.0)],
            ]
            blobs = [(60, 0.5)] * 10
            hand_split.assign_groups(groups, hands, blobs, 384,
                                     primo_activation_bars=primo_activation_bars)
            return [[note['hand'] for note in group] for group in groups]

        for hands in (2, 4):
            with self.subTest(hands=hands):
                self.assertEqual(assignments(hands, 1), assignments(hands, 8))

    def test_primo_keeps_third_hand_empty_outside_reported_segments(self):
        groups = [
            [dict(note=pitch, start_ticks=0, start_seconds=0.0) for pitch in (48, 60, 72)],
            [dict(note=60, start_ticks=768, start_seconds=1.0)],
            [dict(note=pitch, start_ticks=7680, start_seconds=10.0) for pitch in (48, 60, 72)],
            [dict(note=60, start_ticks=18432, start_seconds=24.0)],
        ]
        blobs = [(60, 0.5)] * 50
        cuts_per_group, segments = hand_split.search_splits(
            groups, 3, blobs, 384, limits=(1, 12), primo_activation_bars=4)
        self.assertTrue(segments)
        for start, end in segments:
            self.assertGreaterEqual(end - start + 1, 4)
        bars = [group[0]['start_ticks'] // (384 * 4) + 1 for group in groups]
        for group, bar, cuts in zip(groups, bars, cuts_per_group):
            if not any(start <= bar <= end for start, end in segments):
                self.assertEqual(cuts[1], len(group))
        self.assertTrue(any(cuts[1] < len(group) for group, cuts in zip(groups, cuts_per_group)))

    def test_three_hands_enable_integrated_playable_without_flag(self):
        for extra in ([], ['--playable']):
            with self.subTest(extra=extra):
                args = postprocess.build_parser().parse_args(['song.mid', '--hands', '3', '--no-trim'] + extra)
                steps = postprocess.plan_steps(args)
                names = [name for name, _ in steps]
                self.assertIn('split', names)
                self.assertNotIn('playable', names)
                self.assertIn('integrated playable reduction', dict(steps)['split'])

    def test_two_hands_keep_separate_playable_step(self):
        args = postprocess.build_parser().parse_args(['song.mid', '--hands', '2', '--playable', '--no-trim'])
        self.assertEqual([name for name, _ in postprocess.plan_steps(args)],
                         ['reuse_bpmfix', 'clean', 'playable', 'split'])

    def test_four_hands_still_reject_playable_and_three_hands_need_split(self):
        four = postprocess.build_parser().parse_args(['song.mid', '--hands', '4', '--playable', '--no-trim'])
        with self.assertRaises(ValueError):
            postprocess.plan_steps(four)
        three = postprocess.build_parser().parse_args(['song.mid', '--hands', '3', '--no-split'])
        with self.assertRaises(ValueError):
            postprocess.plan_steps(three)

    def test_gui_three_hands_forces_playable_switch(self):
        def panel_for(hands):
            panel = object.__new__(Panel)
            panel.hands = Mock(get=Mock(return_value=hands))
            panel.switches = {'playable': Mock()}
            panel.step_checks = {'playable': Mock()}
            panel.primo_entry = Mock()
            panel.hands_note = Mock()
            return panel

        three = panel_for('3')
        three.update_hand_options()
        three.switches['playable'].set.assert_called_once_with(True)
        three.step_checks['playable'].configure.assert_called_once_with(state='disabled')
        three.primo_entry.configure.assert_called_once_with(state='readonly')
        three.hands_note.grid.assert_called_once_with()

        two = panel_for('2')
        two.update_hand_options()
        two.switches['playable'].set.assert_not_called()
        two.step_checks['playable'].configure.assert_called_once_with(state='normal')
        two.primo_entry.configure.assert_called_once_with(state='disabled')
        two.hands_note.grid_remove.assert_called_once_with()

        four = panel_for('4')
        four.update_hand_options()
        four.switches['playable'].set.assert_called_once_with(False)
        four.step_checks['playable'].configure.assert_called_once_with(state='disabled')

    def test_gui_presets_normalize_playable_for_three_hands(self):
        self.assertTrue(normalize_preset({'hands': 3, 'switches': {'playable': False}})['switches']['playable'])
        self.assertFalse(normalize_preset({'hands': 4, 'switches': {'playable': True}})['switches']['playable'])
        self.assertFalse(normalize_preset({'hands': 2, 'switches': {'playable': False}})['switches']['playable'])

    def test_integrated_reduction_removes_one_inner_note_for_two_hands(self):
        groups = [self.note_group((36, 48, 60, 72, 84))]
        cuts, segments, details = hand_split.search_splits(
            groups, 3, [(60, 0.5)] * 10, 480, limits=(2, 12), integrated=True, return_details=True)
        self.assertEqual(details['group_primo_active'], [False])
        self.assertEqual(segments, [])
        self.assertEqual([note['note'] for note in details['removed_per_group'][0]], [60])
        self.assertIsNotNone(details['off_best_costs'][0])
        self.assertGreater(details['on_best_costs'][0], details['off_best_costs'][0])
        self.assertEqual(cuts[0][1], 4)

    def test_integrated_primo_handles_group_that_reduction_cannot_solve(self):
        pitches = (36, 40, 60, 64, 84, 88, 108, 112)
        groups = [self.note_group(pitches)]
        cuts, segments, details = hand_split.search_splits(
            groups, 3, [(60, 0.5)] * 10, 480, limits=(2, 12), integrated=True, return_details=True)
        self.assertEqual(details['group_primo_active'], [True])
        self.assertEqual(details['removed_per_group'][0], ())
        self.assertLess(details['on_best_costs'][0], details['off_best_costs'][0])
        self.assertTrue(segments)
        self.assertLess(cuts[0][1], len(pitches))

    def test_integrated_reduction_deletes_notes_from_output_midi(self):
        path = self.root / 'song.mid'
        self.make_midi(path, pitches=(36, 48, 60, 72, 84))
        stats = split_hands_in_place(path, hands=3, limits=(2, 12))
        notes = collect_notes(mido.MidiFile(path))
        self.assertEqual(stats['removed_notes'], 1)
        self.assertEqual(stats['final_notes'], 4)
        self.assertEqual(stats['primo_segments'], [])
        self.assertNotIn(60, [note['note'] for note in notes])
        self.assertEqual({note['start_message'].channel for note in notes}, {0, 2})

    def test_integrated_reduction_records_playable_deletion(self):
        source = self.root / 'song_bpmfix.mid'
        self.make_midi(source, pitches=(36, 40, 44, 48, 52, 84))
        args = postprocess.build_parser().parse_args(
            [str(source), '--hands', '3', '--no-clean', '--no-trim'])
        with contextlib.redirect_stdout(io.StringIO()):
            cleaned, _ = postprocess.run_postprocess(args)
        changes = load_changes(cleaned)
        self.assertTrue(any(change['kind'] == 'deleted' and change['stage'] == 'playable'
                            for change in changes))

    def test_gui_passes_primo_setting_only_for_three_hands(self):
        panel = object.__new__(Panel)
        panel.midi = Mock(get=Mock(return_value='song.mid'))
        panel.source = Mock(get=Mock(return_value='reuse'))
        panel.bpm = Mock(get=Mock(return_value=''))
        panel.audio = Mock(get=Mock(return_value=''))
        preset = default_preset()
        preset['hands'] = 3
        args = panel.argv(preset)
        self.assertEqual(args[args.index('--primo-activation-bars') + 1], '4')
        preset['hands'] = 2
        self.assertNotIn('--primo-activation-bars', panel.argv(preset))

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
