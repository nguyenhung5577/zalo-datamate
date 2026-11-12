from pathlib import Path
import unittest

from datamate.rulebook_check import Guard

ROOT = Path(__file__).resolve().parents[1]


def book(split):
    return (ROOT / split / 'rulebook.md').read_text(encoding='utf-8-sig')


class GuardTest(unittest.TestCase):
    def test_released_books_pass(self):
        for split in ('train', 'public_test'):
            self.assertEqual(Guard(book(split)).changed, [])

    def test_added_words_pass(self):
        text = book('public_test').replace('| Tuổi nợ |', '| Tuổi nợ | thêm một câu mới.', 1)
        self.assertNotIn('Tuổi nợ', Guard(text).changed)

    def test_lost_term_declines_only_its_questions(self):
        guard = Guard(book('public_test').replace('**ngày đặt**, không từ hạn', '**hạn thanh toán**, không từ hạn', 1))
        self.assertIn('Tuổi nợ', guard.changed)
        self.assertTrue(guard.blocks({'question': 'Nợ theo tuổi nợ thế nào?'}))
        self.assertFalse(guard.blocks({'question': 'Doanh thu tháng 3/2026 là bao nhiêu?'}))


if __name__ == '__main__':
    unittest.main()
