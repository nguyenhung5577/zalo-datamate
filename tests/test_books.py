from datetime import date
import unittest

from datamate.books import Contract, due_date


class ContractTest(unittest.TestCase):
    def setUp(self):
        # Bought 5 Jul, 900 left over 3 installments of 300, due on the 3rd from August.
        self.contract = Contract('a', date(2025, 7, 5), 1000, 100, 3, 300, 3)
        self.contract.paid.append((date(2025, 8, 3), 300))

    def test_first_due_date_is_the_next_month(self):
        self.assertEqual(due_date(date(2025, 7, 5), 1, 3), date(2025, 8, 3))
        self.assertEqual(due_date(date(2025, 12, 5), 2, 31), date(2026, 2, 28))

    def test_remaining_counts_installments_not_yet_due(self):
        self.assertEqual(self.contract.remaining(date(2025, 8, 31)), 600)

    def test_due_on_the_day_is_due_but_not_overdue(self):
        self.assertEqual(self.contract.due_steps(date(2025, 9, 3)), 2)
        self.assertEqual(self.contract.overdue(date(2025, 9, 3)), 0)
        self.assertEqual(self.contract.overdue(date(2025, 9, 4)), 300)
