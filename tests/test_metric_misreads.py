import unittest

from datamate.metrics import check_evidence


def read(metric, text, period=None):
    slots = {'metric': metric, 'period': period or {'kind': 'today'}, 'filters': {}}
    check_evidence(slots, text)
    return slots


class MisreadTest(unittest.TestCase):
    def test_spoken_periods_are_not_today(self):
        self.assertEqual(read('revenue', 'Th nay nhom khach mua tong bnhiu dthu?')['period'], {'kind': 'this_month'})
        self.assertEqual(read('orders', 'Dau nam den hnay tong so dh bnhiu?')['period'], {'kind': 'year_to_date'})
        self.assertEqual(read('revenue', 'Năm nay tính tới hôm nay bán được tổng cộng bao nhiêu tiền?')['period'], {'kind': 'year_to_date'})
        self.assertEqual(read('revenue', '30 ngày qua kể cả hôm nay dthu đc bnhiu?')['period'], {'kind': 'last_days', 'days': 30})

    def test_average_is_not_a_total(self):
        self.assertEqual(read('revenue', 'Dthu bq mỗi đơn tuần trc là bnhiu?')['metric'], 'average_order')

    def test_last_year_wording(self):
        slots = read('growth', 'Doanh thu tháng 7/2026 so với tháng 7 năm rồi tăng hay giảm mấy %?')
        self.assertEqual(slots['against'], 'last_year')


if __name__ == '__main__':
    unittest.main()
