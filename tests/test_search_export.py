import json
import unittest
from pathlib import Path
from corpus import cutoff


class CorpusTests(unittest.TestCase):
    def test_calendar_cutoff_fixture(self):
        for case in json.loads((Path(__file__).parent / 'fixtures/corpus-cutoff.json').read_text()):
            with self.subTest(case=case):
                self.assertEqual(cutoff(case['timestamp']), case['cutoff'])
