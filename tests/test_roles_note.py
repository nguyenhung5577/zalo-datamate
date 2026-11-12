import unittest

from datamate.roles import column_roles


class SalesNoteTest(unittest.TestCase):
    def test_plain_note_on_a_sales_book_is_the_order_note(self):
        roles = column_roles({'table': 'sobanhang'}, ['Ngày', 'HĐ', 'Tên hàng', 'Số lượng', 'Thành tiền', 'Ghi chú'], {})
        self.assertEqual(roles.get('Ghi chú'), 'order_note')

    def test_status_column_keeps_the_note_out(self):
        roles = column_roles({'table': 'sobanhang'}, ['Ngày', 'HĐ', 'Tên hàng', 'Số lượng', 'Trạng thái', 'Ghi chú'], {})
        self.assertNotEqual(roles.get('Ghi chú'), 'order_note')
