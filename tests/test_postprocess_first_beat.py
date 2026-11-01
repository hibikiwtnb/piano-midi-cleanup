"""The runner's first beat step: estimated by default, or a given value shared by several transcriptions."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import postprocess


def steps(*argv):
    return [description for name, description in postprocess.plan_steps(postprocess.build_parser().parse_args(
        ['song.mid', '--bpm', '165', *argv])) if name == 'first_beat']


class FirstBeatStep(unittest.TestCase):
    def test_default_estimates_the_first_beat(self):
        self.assertEqual(steps(), ['Auto first beat'])

    def test_given_value_replaces_the_estimate(self):
        self.assertEqual(steps('--first-beat', '-0.216'), ['First beat at -0.216s'])

    def test_given_value_and_no_first_beat_conflict(self):
        with self.assertRaises(SystemExit):
            postprocess.build_parser().parse_args(['song.mid', '--bpm', '165', '--no-first-beat', '--first-beat', '0'])


if __name__ == '__main__':
    unittest.main()
