"""Behaviour of the answer comparison: the video's judging rule and the pooled straight-line alignment."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from compare_answer import fit_alignment, match


def note(start, pitch):
    return dict(start=start, end=start + 0.2, pitch=pitch, velocity=80)


class CompareAnswer(unittest.TestCase):
    def test_hit_needs_same_pitch_within_50_ms(self):
        answer = [note(1.0, 60), note(2.0, 62), note(3.0, 64)]
        model = [note(1.04, 60), note(2.07, 62), note(3.0, 65)]
        self.assertEqual(match(answer, model), [(0, 0)])

    def test_matching_is_one_to_one(self):
        # Two model notes near one answer note: one hit, the other stays an extra.
        answer = [note(1.0, 60)]
        model = [note(0.98, 60), note(1.02, 60)]
        self.assertEqual(len(match(answer, model)), 1)

    def test_one_to_one_prefers_the_assignment_that_keeps_both_hits(self):
        # Greedy nearest matching would pair answer 1.03 with model 1.0 and lose answer 0.97.
        answer = [note(0.97, 60), note(1.03, 60)]
        model = [note(1.0, 60), note(1.07, 60)]
        self.assertEqual(sorted(match(answer, model)), [(0, 0), (1, 1)])

    def test_alignment_recovers_offset_and_rate_from_pooled_models(self):
        answer = [note(i * 0.25, 60 + i % 7) for i in range(40)]
        model_a = [note(5.0 + 1.01 * n['start'], n['pitch']) for n in answer[::2]]
        model_b = [note(5.0 + 1.01 * n['start'], n['pitch']) for n in answer[1::2]]
        offset, rate, hits = fit_alignment(answer, [model_a, model_b])
        self.assertAlmostEqual(offset, 5.0, places=2)
        self.assertAlmostEqual(rate, 1.01, places=3)
        self.assertEqual(hits, 40)


if __name__ == '__main__':
    unittest.main()
