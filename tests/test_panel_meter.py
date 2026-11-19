"""Tests for the panel time signature menu, auto default, and saved overrides."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import mido
import postprocess
import postprocess_panel
from postprocess_panel import Panel


class PanelMeterTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def make_waltz_midi(self, path, bars=12):
        midi = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        midi.tracks.append(track)
        track.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(120), time=0))
        track.append(mido.MetaMessage('time_signature', numerator=4, denominator=4, time=0))
        events = []
        for bar in range(bars):
            for beat in range(3):
                tick = (bar * 3 + beat) * 480
                pitch = 48 if beat == 0 else 64
                velocity = 105 if beat == 0 else 55
                events.append((tick, mido.Message('note_on', note=pitch, velocity=velocity, time=0)))
                events.append((tick + 240, mido.Message('note_off', note=pitch, velocity=0, time=0)))
        events.sort(key=lambda item: item[0])
        last_tick = 0
        for tick, message in events:
            track.append(message.copy(time=tick - last_tick))
            last_tick = tick
        midi.save(path)

    def open_panel(self, settings=None):
        settings_path = self.root / 'panel.json'
        if settings is not None:
            settings_path.write_text(json.dumps(settings), encoding='utf-8')
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with patch.object(postprocess_panel, 'SETTINGS_PATH', str(settings_path)), \
                patch.object(Panel, 'build_run'):
            panel = Panel(root)
        return panel, settings_path

    def test_panel_meter_defaults_to_auto_and_keeps_language_sync(self):
        panel, settings_path = self.open_panel()
        self.assertEqual(tuple(panel.meter_box['values']), ('自動', '4/4', '3/4', '6/8'))
        self.assertEqual(panel.meter.get(), '自動')
        self.assertEqual(panel.selected_meter(), 'auto')

        with patch.object(postprocess_panel, 'SETTINGS_PATH', str(settings_path)), \
                patch.object(Panel, 'build_run'):
            for language, expected_label in (('English', 'Auto'), ('简体中文', '自动'), ('繁體中文', '自動')):
                panel.language.set(language)
                panel.change_language()
                self.assertEqual(panel.meter_box['values'][0], expected_label)
                self.assertEqual(panel.meter.get(), expected_label)
                self.assertEqual(panel.selected_meter(), 'auto')

        midi_path = self.root / 'waltz.mid'
        self.make_waltz_midi(midi_path)
        panel.midi.set(str(midi_path))
        panel.source.set('bpm')
        panel.bpm.set('120')
        argv = panel.argv(panel.current_preset())
        self.assertIn('--time-signature', argv)
        self.assertEqual(argv[argv.index('--time-signature') + 1], 'auto')

        with contextlib.redirect_stdout(io.StringIO()):
            postprocess.run_postprocess(postprocess.build_parser().parse_args(argv))
        report = (self.root / 'waltz_stats.txt').read_text(encoding='utf-8')
        self.assertIn('Time signature: 3/4 (auto,', report)

    def test_panel_reads_saved_meter_and_allows_manual_override(self):
        for saved in ('4/4', '3/4', '6/8'):
            with self.subTest(saved=saved):
                panel, _ = self.open_panel({'time_signature': saved})
                self.assertEqual(panel.meter.get(), saved)
                self.assertEqual(panel.selected_meter(), saved)
                midi_path = self.root / f'override_{saved.replace("/", "_")}.mid'
                self.make_waltz_midi(midi_path)
                panel.midi.set(str(midi_path))
                panel.source.set('bpm')
                panel.bpm.set('120')
                argv = panel.argv(panel.current_preset())
                self.assertEqual(argv[argv.index('--time-signature') + 1], saved)
                with contextlib.redirect_stdout(io.StringIO()):
                    postprocess.run_postprocess(postprocess.build_parser().parse_args(argv))
                report = midi_path.with_name(midi_path.stem + '_stats.txt').read_text(encoding='utf-8')
                self.assertIn(f'Time signature: {saved}\n', report)


if __name__ == '__main__':
    unittest.main()
