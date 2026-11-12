import unittest

from datamate.normalized import split_toppings, status
from datamate.roles import canonicalize, column_roles, glossary, key


class RoleTests(unittest.TestCase):
    def test_key_splits_case_and_accents(self):
        self.assertEqual(key('SLQuyDoi'), 'sl quy doi')
        self.assertEqual(key('ĐVT'), 'dvt')

    def test_glossary_outranks_name_and_patterns_match_files(self):
        terms = glossary('## Cột trong `Ban_T<tháng>.xlsx`, sheet `Don`\n- **Mã GD**: mã đơn\n')
        table = {'table': 'ban_t1', 'source': 'Ban_T1.xlsx', 'sheet': 'Don'}
        self.assertEqual(column_roles(table, ['Mã GD'], terms), {'Mã GD': 'order_id'})

    def test_generic_date_follows_document_kind(self):
        roles = column_roles({'table': 'x', 'source': 'x'}, ['Mã nhập hàng', 'Thời gian'], {})
        self.assertEqual(roles['Thời gian'], 'import_date')

    def test_repeated_headers_dropped_and_values_stripped(self):
        rows = [{'Mã hàng': 'Mã hàng', 'Số lượng': 'Số lượng', '__dm_row': '2'},
                {'Mã hàng': ' SP1 ', 'Số lượng': '1', '__dm_row': '3'}]
        out = canonicalize({'table': 'x', 'source': 'x'}, rows, {})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['Mã hàng'], 'SP1')

    def test_status_uses_whole_labels(self):
        self.assertEqual(status('Đang vận chuyển'), 'shipping')
        self.assertEqual(status('Đã hủy đơn'), 'cancelled')
        with self.assertRaises(ValueError):
            status('trầy nhẹ')

    def test_toppings_split_from_dish(self):
        self.assertEqual(split_toppings('Latte (M) + 2 Kem cheese (25.000đ)'),
                         ('Latte (M)', [(2, 'Kem cheese', 25000.0)]))
        self.assertEqual(split_toppings('Trà + chanh'), ('Trà + chanh', []))


if __name__ == '__main__':
    unittest.main()
