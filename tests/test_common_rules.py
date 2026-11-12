import unittest

from datamate.direct import spoken, untyped


class TypedWordsTest(unittest.TestCase):
    def test_stray_key_strokes_are_removed(self):
        for typed, plain in [('soos', 'so'), ('ddown', 'don'), ('thangs', 'thang'), ('toongr', 'tong'), ('hangf', 'hang')]:
            self.assertEqual(untyped(typed), plain)

    def test_unknown_words_stay(self):
        self.assertEqual(untyped('samsung'), 'samsung')
        self.assertEqual(spoken('so luot'), 'so luot')


if __name__ == '__main__':
    unittest.main()
