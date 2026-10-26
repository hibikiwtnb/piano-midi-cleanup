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
