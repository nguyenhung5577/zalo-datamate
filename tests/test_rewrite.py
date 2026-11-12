import json
from types import SimpleNamespace
import unittest

from datamate.rewrite import rewrite


def replying(text):
    return SimpleNamespace(chat=lambda *a, **k: (json.dumps({'question': text}, ensure_ascii=False), {}, 'stop'))


class RewriteTest(unittest.TestCase):
    question = {'question': 'dt th 3/2025 bn', 'as_of_date': '2025-04-01'}

    def test_plain_wording_is_kept(self):
        text, _ = rewrite(replying('Doanh thu tháng 3/2025 là bao nhiêu?'), self.question)
        self.assertEqual(text, 'Doanh thu tháng 3/2025 là bao nhiêu?')

    def test_a_changed_number_drops_the_rewrite(self):
        self.assertIsNone(rewrite(replying('Doanh thu tháng 4/2025 là bao nhiêu?'), self.question)[0])

    def test_a_changed_period_drops_the_rewrite(self):
        question = {'question': 'dt hqua bn', 'as_of_date': '2025-04-01'}
        self.assertEqual(rewrite(replying('Doanh thu hôm qua là bao nhiêu?'), question)[0], 'Doanh thu hôm qua là bao nhiêu?')
        self.assertIsNone(rewrite(replying('Doanh thu hàng qua là bao nhiêu?'), question)[0])

    def test_a_changed_month_list_drops_the_rewrite(self):
        question = {'question': 'dthu 3 thang 5/2025, 6/2025, 7/2025 co tang lien tuc khong', 'as_of_date': '2025-08-01'}
        self.assertIsNone(rewrite(replying('Doanh thu tháng 3, tháng 5/2025, tháng 6/2025, tháng 7/2025 có tăng liên tục không?'), question)[0])
        self.assertIsNotNone(rewrite(replying('Doanh thu 3 tháng 5/2025, 6/2025, 7/2025 có tăng liên tục không?'), question)[0])

    def test_a_lost_metric_word_drops_the_rewrite(self):
        question = {'question': '30 ngày qua kể cả hôm nay dthu đc bnhiu?', 'as_of_date': '2025-04-01'}
        self.assertIsNone(rewrite(replying('30 ngày qua kể cả hôm nay đã thu được bao nhiêu?'), question)[0])
        self.assertIsNotNone(rewrite(replying('Doanh thu 30 ngày qua kể cả hôm nay được bao nhiêu?'), question)[0])
        question = {'question': 'T7/2025 tiem giao duoc bao nhieu dot hang?', 'as_of_date': '2025-08-01'}
        self.assertIsNone(rewrite(replying('Tháng 7/2025 tiệm giao được bao nhiêu đơn hàng?'), question)[0])
        question = {'question': 'Th3/2026, dthu các đơn trả xi ô đi và các đơn trả ví điện tử lần lượt là bnhiu?', 'as_of_date': '2026-04-01'}
        self.assertIsNone(rewrite(replying('Tháng 3/2026, doanh thu các đơn trả bằng tiền mặt và các đơn trả bằng ví điện tử lần lượt là bao nhiêu?'), question)[0])

    def test_the_same_words_or_no_text_change_nothing(self):
        self.assertIsNone(rewrite(replying('DT th 3/2025 bn'), self.question)[0])
        self.assertIsNone(rewrite(replying(''), self.question)[0])


if __name__ == '__main__':
    unittest.main()
