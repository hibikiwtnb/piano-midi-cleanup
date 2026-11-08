"""The optional Steinway output resolves its SFZ samples before sfizz loads it."""

import re
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import sfizz_output


class SfizzOutputTest(unittest.TestCase):
    def setUp(self):
        self.path = sfizz_output.sfz_path()
        if self.path is None:
            self.skipTest('Steinway Grand Piano SFZ is not installed')

    def test_sfz_samples_resolve(self):
        text = sfizz_output._sfz_text(self.path)
        match = re.search(r'default_path\s*=\s*([^\r\n]*)', text)
        folder = self.path.parent / (match.group(1).strip() if match else '')
        sample = re.search(r'sample\s*=\s*([^\s]+)', text)
        self.assertIsNotNone(sample)
        self.assertTrue((folder / sample.group(1)).is_file())


if __name__ == '__main__':
    unittest.main()
