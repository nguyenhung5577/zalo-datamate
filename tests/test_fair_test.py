import unittest

from scripts.fair_test import held_out, noisy


class FairTest(unittest.TestCase):
    def test_even_shops_are_held_out(self):
        self.assertTrue(held_out('biz_102'))
        self.assertFalse(held_out('biz_101'))

    def test_noise_is_the_same_every_run(self):
        question = {'id': 'q_1', 'question': 'Tháng trước cửa hàng bán được bao nhiêu sản phẩm?'}
        self.assertEqual(noisy(question), noisy(question))

    def test_noise_changes_words_not_meaning(self):
        changed = [noisy({'id': f'q_{i}', 'question': 'Doanh thu hôm qua bao nhiêu?'}) for i in range(40)]
        self.assertGreater(len(set(changed)), 4)
        self.assertTrue(all(text.endswith('?') for text in changed))


if __name__ == '__main__':
    unittest.main()
