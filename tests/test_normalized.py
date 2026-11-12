import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from datamate.normalized import NormalizedData, name_key, parse_date, parse_number, status


class NormalizationTests(unittest.TestCase):
    def test_number_formats_and_rejection(self):
        for value, expected in [('45.000đ', 45000), ('1tr440', 1440000), ('10k', 10000),
                                ('16,56tr', 16560000), ('-2tr250', -2250000), ('-45k', -45000),
                                ('45,000.0000', 45000), ('1.234,50', 1234.5)]:
            self.assertEqual(parse_number(value), expected)
        self.assertEqual(parse_number('1 chai', quantity=True), 1)
        self.assertEqual(parse_number('0,9 tấn', quantity=True), 0.9)
        self.assertEqual(parse_number('1 bao 25kg', quantity=True), 1)
        self.assertEqual(parse_number('2 hộp 30', quantity=True), 2)
        for value in ['unknown', 'NaN', '5+5', '1trX']:
            with self.assertRaises((ValueError, ArithmeticError)):
                parse_number(value)

    def test_dates_and_statuses(self):
        self.assertEqual(parse_date('28/4', 'Tháng 04-2025'), '2025-04-28')
        self.assertEqual(parse_date('45658'), '2025-01-01')
        self.assertEqual(parse_date('6 thg 5 2026'), '2026-05-06')
        self.assertEqual(status('Đã hoàn hàng'), 'returned')
        self.assertEqual(status('returned'), 'returned')
        self.assertEqual(status('Hoàn thành'), 'completed')
        self.assertEqual(status('Đang giao dịch'), 'shipping')
        with self.assertRaises(ValueError):
            parse_date('28/4')
        with self.assertRaises(ValueError):
            status('mystery')

    def test_group_inheritance_aliases_returns_and_lineage(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'raw.sqlite'
            columns = ['Mã đơn hàng', 'Ngày đặt hàng', 'Trạng thái đơn hàng', 'Tên khách hàng',
                       'Tên sản phẩm', 'Số lượng sản phẩm', 'Giá sản phẩm', 'Tổng tiền', 'Đơn vị tính',
                       '__dm_file', '__dm_sheet', '__dm_row']
            values = [
                ['O1', '01/01/2025', 'xong', 'An', 'Tiger lon', '1', '20.000đ', '20.000đ', 'lon', 'sales.csv', '', '2'],
                [None, None, None, None, 'Tiger  Lon', '2', '20.000đ', '40.000đ', 'lon', 'sales.csv', '', '3'],
                ['O2', '02/01/2025', 'shipping', 'Bình', 'Tiger lon', '1', '20.000đ', '20.000đ', 'lon', 'sales.csv', '', '4'],
                [None, None, None, None, 'TỔNG NGÀY 02/01', None, None, '80.000đ', None, 'sales.csv', '', '5'],
            ]
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE sales (' + ','.join('"' + c + '" TEXT' for c in columns) + ')')
            db.executemany('INSERT INTO sales VALUES (' + ','.join('?' for _ in columns) + ')', values)
            db.execute('CREATE TABLE lots ("Mã đơn hàng" TEXT,"Mã lô" TEXT,"Số lượng" TEXT,"Số lượng trả" TEXT,__dm_file TEXT,__dm_sheet TEXT,__dm_row TEXT)')
            db.execute("INSERT INTO lots VALUES ('O2','L1','1','1','lots.csv','','2')")
            db.commit()
            before = path.read_bytes()
            raw = SimpleNamespace(path=path, cache=root, readme='Sales.', tables=[{'table': 'sales'}, {'table': 'lots'}])
            data = NormalizedData(raw)
            self.assertEqual(data.query('SELECT SUM(line_revenue), SUM(basic_quantity) FROM sales')['rows'], [[60000, 3]])
            self.assertEqual(data.query('SELECT COUNT(*) FROM products')['rows'], [[1]])
            self.assertEqual(data.query("SELECT status FROM orders WHERE order_id='O2'")['rows'], [['returned']])
            self.assertEqual(data.report['quality']['inherited_order_ids'], 1)
            self.assertEqual(data.report['quality']['summary_rows_skipped'], 1)
            self.assertEqual(json.loads(data.query('SELECT lineage FROM order_lines LIMIT 1')['rows'][0][0])['row'], '2')
            self.assertEqual(path.read_bytes(), before)

            with self.assertRaises(sqlite3.DatabaseError):
                data.query('DELETE FROM orders')
            db.close()


if __name__ == '__main__':
    unittest.main()
