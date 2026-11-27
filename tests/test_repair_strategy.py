"""Every review code has a repair route, and candidate metrics are deterministic evidence only."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import repair_strategy
from test_measure_quality import DATA_REPO, LEFT, RIGHT, write_midi


FIELDS = ('meaning', 'causes', 'candidates', 'evidence', 'agent', 'keep_when', 'never_auto', 'verify')


class RepairStrategyTest(unittest.TestCase):
    def test_every_emitted_code_has_a_complete_strategy(self):
        self.assertGreaterEqual(len(repair_strategy.emitted_codes()), 27)
        self.assertEqual(repair_strategy.uncovered_codes(), [])
        for code, strategy in repair_strategy.STRATEGIES.items():
            for field in FIELDS:
                self.assertTrue(strategy.get(field), '{} lacks {}'.format(code, field))

    def _route(self, notes):
        with tempfile.TemporaryDirectory(dir=DATA_REPO) as folder:
            path = str(Path(folder) / 'case.mid')
            write_midi(path, notes)
            return repair_strategy.route(path)['issues']

    def test_octave_error_in_bass_line_prefers_octave_move_over_deletion(self):
        # Bass line D2 E2 F#2 with one D1 among it, plus an upper note for a two-note figure.
        notes = [(0, 38, 0.5, LEFT), (0.5, 40, 0.5, LEFT), (1, 26, 0.5, LEFT), (1, 45, 0.5, RIGHT),
                 (1.5, 42, 0.5, LEFT), (2, 43, 0.5, LEFT)]
        issue = next(item for item in self._route(notes) if item['code'] == 'octave_pattern_outlier')
        by_action = {(item['action'], item.get('new_pitch')): item for item in issue['candidates']
                     if item['metrics'] and item.get('note', {}).get('name') in (None, 'D1')}
        keep = by_action[('keep', None)]['metrics']
        octave = by_action[('octave', 'D2')]['metrics']
        self.assertLess(octave['max_leap_to_neighbours'], keep['max_leap_to_neighbours'])
        self.assertGreaterEqual(octave['best_pattern_match'], keep['best_pattern_match'])

    def test_counter_example_repeated_open_voicing_keeps_the_best_pattern(self):
        # The same D/F# open voicing struck four times: a style, not an error.
        chord = (50, 54, 62, 66)
        notes = [(beat, pitch, 0.5, LEFT if pitch < 60 else RIGHT) for beat in (0, 1, 2, 3) for pitch in chord]
        issue = next(item for item in self._route(notes) if item['code'] == 'unusual_chord_spacing')
        keep = next(item for item in issue['candidates'] if item['action'] == 'keep')['metrics']
        others = [item['metrics']['best_pattern_match'] for item in issue['candidates']
                  if item['metrics'] and item['action'] != 'keep']
        self.assertEqual(keep['best_pattern_match'], 1.0)
        self.assertTrue(all(value < 1.0 for value in others))

    def test_score_only_codes_route_to_notation(self):
        for code in ('unnecessary_tie_chain', 'unnecessary_beam_break', 'grace_note_candidate'):
            self.assertIn('notation_only', repair_strategy.STRATEGIES[code]['candidates'])
            self.assertNotIn('delete', repair_strategy.STRATEGIES[code]['candidates'])


if __name__ == '__main__':
    unittest.main()
