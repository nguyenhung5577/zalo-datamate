import sqlite3
import unittest

from datamate.direct import Direct
from datamate.metrics import Metrics


class Data:
    report = {'source_capabilities': ['line_items']}

    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript("""
            CREATE TABLE products (product_id, product_name, base_unit, is_service, active_ingredient, prescription);
            CREATE TABLE customers (customer_id, customer_name);
            CREATE TABLE orders (order_id, order_date, created_at, status, customer_id);
            CREATE TABLE purchase_lines (product_id, import_date, purchase_unit, quantity, unit_price, recorded_total, source_status);
            CREATE TABLE sales (order_id, product_id, product_name, basic_quantity, line_revenue, order_date, customer_id, customer_name, is_service);
            CREATE TABLE daily_revenue (order_date, revenue, order_count);
            CREATE TABLE order_lines (order_id, product_id, sale_unit);
            INSERT INTO order_lines VALUES (1, 'a', 'lon'), (2, 'b', 'hop');
            INSERT INTO products VALUES ('a', 'Bia Tiger', 'lon', 0, NULL, 0), ('b', 'Thuoc X', 'hop', 0, 'Ibuprofen', 1);
            INSERT INTO customers VALUES ('c1', 'An'), ('c2', 'Binh');
            INSERT INTO orders VALUES (1, '2025-01-02', NULL, 'completed', NULL), (2, '2025-01-03', NULL, 'completed', NULL), (3, '2025-02-01', NULL, 'completed', NULL),
                (10, '2025-03-05', '2025-03-05 08:00', 'completed', 'c1'), (11, '2025-03-05', '2025-03-05 08:30', 'completed', 'c1'),
                (12, '2025-03-06', '2025-03-06 09:00', 'completed', 'c1'), (13, '2025-03-07', '2025-03-07 10:00', 'completed', 'c2'),
                (14, '2025-03-07', '2025-03-07 10:10', 'completed', 'c2'), (15, '2025-03-07', '2025-03-07 10:20', 'completed', 'c2'),
                (16, '2025-03-08', '2025-03-08 11:00', 'cancelled', 'c2'), (17, '2025-03-31', '2025-03-31 11:00', 'cancelled', 'c2');
            INSERT INTO purchase_lines VALUES ('a', '2025-01-05', 'thung', 1, 100, NULL, 'done'), ('a', '2025-01-06', 'thung', 3, 200, NULL, 'done'),
                ('a', '2025-01-07', 'thung', 9, 900, NULL, 'huy');
            INSERT INTO sales VALUES (1, 'a', 'Bia Tiger', 4, 40, '2025-01-02', NULL, NULL, 0), (2, 'b', 'Thuoc X', 1, 60, '2025-01-03', NULL, NULL, 0),
                (3, 'a', 'Bia Tiger', 6, 60, '2025-02-01', NULL, NULL, 0),
                (10, 'a', 'Bia Tiger', 1, 100, '2025-03-05', 'c1', 'An', 0), (11, 'a', 'Bia Tiger', 1, 100, '2025-03-05', 'c1', 'An', 0),
                (12, 'b', 'Thuoc X', 1, 100, '2025-03-06', 'c1', 'An', 0), (13, 'a', 'Bia Tiger', 1, 900, '2025-03-07', 'c2', 'Binh', 0),
                (14, 'a', 'Bia Tiger', 1, 900, '2025-03-07', 'c2', 'Binh', 0), (15, 'a', 'Bia Tiger', 1, 900, '2025-03-07', 'c2', 'Binh', 0);
            INSERT INTO daily_revenue VALUES ('2025-01-02', 40, 1), ('2025-01-03', 60, 1), ('2025-03-05', 200, 2), ('2025-03-06', 100, 1), ('2025-03-07', 2700, 3);
            ALTER TABLE sales ADD COLUMN sale_unit;
        """)

    def query(self, sql, params, timeout=None, max_rows=None):
        return {'rows': self.db.execute(sql, params).fetchall()}


def ask(text):
    return Direct(Metrics(Data())).answer({'question': text, 'as_of_date': '2025-03-31'})


class DirectTest(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(ask('thang 1/2025 ban duoc may lon bia tiger')['answer'], 4)
        self.assertEqual(ask('ngay nao trong thang 1/2025 dthu cao nhat')['answer'], '2025-01-03')
        self.assertEqual(ask('gia nhap binh quan mot thung bia tiger thang 1/2025 la bn')['answer'], 175)
        self.assertEqual(ask('dthu thuoc ke don thang 1/2025 la bn')['answer'], 60)
        self.assertEqual(ask('dthu hang k ke don thang 1/2025 la bn')['answer'], 40)
        self.assertEqual(ask('doanh thu thuoc hoat chat ibuprofen thang 1/2025 la bn')['answer'], 60)
        self.assertEqual(ask('tu thang 1/2025 den thang 2/2025 doanh thu moi thang la bn')['answer'], [100, 60])


    def test_counts_over_a_threshold(self):
        self.assertEqual(ask('thang 3/2025 co may ngay dthu tu 200 dong tro len')['answer'], 2)
        self.assertEqual(ask('thang 3/2025 co may don doanh thu tu 900 dong tro len')['answer'], 3)
        self.assertEqual(ask('thang 3/2025 co may khach mua tu 3 don tro len')['answer'], 2)
        self.assertEqual(ask('thang 3/2025 co may mat hang co mat trong it nhat 5 don')['answer'], 1)

    def test_units_and_top_items(self):
        self.assertEqual(ask('thang 1/2025 ban duoc may lon')['answer'], 4)
        self.assertIsNone(ask('thang 1/2025 bia lon ban duoc may lon'))
        self.assertEqual(ask('thang 3/2025 mat hang nao thu ve nhieu tien nhat')['answer'], 'Bia Tiger')
        self.assertEqual(ask('thang 3/2025 top 2 mon ban chay nhat')['answer'], ['Bia Tiger', 'Thuoc X'])
        self.assertEqual(ask('thang 1/2025, thang 2/2025 thang nao ban nhieu tien nhat')['type'], 'date')
        self.assertIsNone(ask('top nam mon ban chay nhat thang 3/2025'))
        self.assertEqual(ask('thang 3/2025 san pham nao ngon nhat')['type'], 'unanswerable')
        self.assertIsNone(ask('thang 3/2025 sua bot cua hang nao ban nhieu lon nhat'))

    def test_plain_totals_read_every_word(self):
        self.assertEqual(ask('thang 1/2025 doanh thu tiem duoc bao nhieu')['answer'], 100)
        self.assertEqual(ask('thang 1/2025 co may don')['answer'], 2)
        self.assertIsNone(ask('thang 1/2025 doanh thu khach quen duoc bao nhieu'))

    def test_month_lists_stay_whole(self):
        direct = Direct(Metrics(Data()))
        as_of = '2025-03-31'
        self.assertEqual(direct.month_list('dthu thang 1, 2, thang 3/2025', as_of), [(1, 2025), (2, 2025), (3, 2025)])
        self.assertEqual(direct.month_list('dthu thang 1, 2, 3 nam 2025', as_of), [(1, 2025), (2, 2025), (3, 2025)])
        self.assertIsNone(direct.month_list('den tuyp thang 8 1.2m 18w rang dong trong thang 4 nam 2025 la bao nhieu', as_of))

    def test_spoken_forms(self):
        self.assertEqual(ask('thang 3/2025 co may khach mua tu 3 don'), None)
        self.assertEqual(ask('thang 3/2025 bao nhieu khach mua it nhat 3 don')['answer'], 2)

    def test_rulebook_superlatives(self):
        self.assertEqual(ask('thang 3/2025 hang nao duoc ua chuong nhat')['answer'], 'Bia Tiger')
        self.assertEqual(ask('thang 3/2025 khach trung thanh nhat la ai')['answer'], 'An')
        self.assertEqual(ask('thang 3/2025 khach sop nhat la ai')['answer'], 'Binh')
        self.assertEqual(ask('thang 3/2025 khach hay doi y nhat la ai')['answer'], 'Binh')
        self.assertEqual(ask('thang 3/2025 ngay dat khach nhat')['answer'], '2025-03-07')
        self.assertEqual(ask('thang 3/2025 gio cao diem la may gio')['answer'], 10)
        self.assertIsNone(ask('thang 1/2025 hang nao duoc ua chuong nhat'))  # one order each: a tie
        self.assertEqual(ask('trong thang 1/2025, thang 2/2025 thang nao dthu cao nhat')['answer'], '2025-01')
        self.assertEqual(ask('tu thang 12/2024 den thang 3/2025 thang nao dthu cao nhat')['answer'], '2025-03')

    def test_declines_what_it_cannot_read_fully(self):
        self.assertIsNone(ask('thang 1/2025 ca sang ban duoc may lon bia tiger'))
        self.assertIsNone(ask('thang 1/2025 lay so nhap tru so ban cua bia tiger thi dc may lon'))
        self.assertIsNone(ask('thang 1/2025 ban duoc may lon bia'))
        self.assertIsNone(ask('ngay nao trong thang 1/2025 dthu thap nhat'))


if __name__ == '__main__':
    unittest.main()
