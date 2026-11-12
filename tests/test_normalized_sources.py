import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from datamate.normalized import NormalizedData


class NormalizedSourceTests(unittest.TestCase):
    def fixture(self, directory, extra_drug=None, missing_header=False):
        root = Path(directory)
        path = root / 'raw.sqlite'
        tables = []
        with sqlite3.connect(path) as db:
            def table(name, columns, rows):
                all_columns = columns + ['__dm_file', '__dm_sheet', '__dm_row']
                db.execute('CREATE TABLE '+name+' ('+','.join('"'+c+'" TEXT' for c in all_columns)+')')
                db.executemany('INSERT INTO '+name+' VALUES ('+','.join('?' for _ in all_columns)+')',
                    [row+[name+'.xlsx','Sheet1',str(index)] for index,row in enumerate(rows,2)])
                tables.append({'table': name})
            table('catalog', ['Mã SKU','Tên sản phẩm','Loại sản phẩm','Đơn vị'],
                  [['P1','Brand one','drug','tablet'],['P2','Brand two','drug','tablet']])
            table('drugs', ['Mã SKU','Hoạt chất','Thuốc kê đơn'],
                  [['P1','Cetirizin + Other','1'],['P2',None,'0']] + (extra_drug or []))
            table('sales', ['Mã đơn hàng','Ngày đặt hàng','Trạng thái đơn hàng','Tên sản phẩm',
                           'Số lượng sản phẩm','Giá sản phẩm','Tổng tiền','Đơn vị tính'],
                  [['O1','01/04/2025','xong','Brand one','1','100','100','tablet']])
            table('conversions', ['Mã hàng','Đơn vị quy đổi','Tỉ lệ quy đổi','Đơn vị cơ bản'],
                  [['P1','box','12','tablet']])
            table('headers', ['Mã nhập hàng','Thời gian','Trạng thái'],
                  [] if missing_header else [['I1','02/04/2025','Đã nhập hàng']])
            table('details', ['Mã nhập hàng','STT','Mã hàng','ĐVT','Số lượng','Đơn giá','Thành tiền'],
                  [['I1','1','P1','box','1','20','19'],['I1','2','P1','box','3','40','120'],
                   ['I1','3','P1','tablet','24','2','48'],['I1','4','P2','tablet','5',None,None]])
            table('flat', ['Số phiếu','Ngày nhập','Product','ĐVT','SL nhập','Giá nhập','Thành tiền'],
                  [['I2','03/04/2025','Brand two','tablet','0,5','1.000đ','500đ']])
        return SimpleNamespace(path=path, cache=root, readme='Fixture.', tables=tables)

    def test_ingredient_and_purchase_values_units_weighting_and_lineage(self):
        with TemporaryDirectory() as directory:
            raw = self.fixture(directory)
            before = raw.path.read_bytes()
            data = NormalizedData(raw)
            self.assertEqual(data.query("SELECT product_name,active_ingredient,prescription FROM products WHERE product_id='P1'")['rows'],
                             [['Brand one','Cetirizin + Other',1]])
            self.assertEqual(data.query("SELECT product_id FROM products WHERE active_ingredient='Cetirizin + Other'")['rows'], [['P1']])
            self.assertEqual(data.query("SELECT product_id FROM products WHERE active_ingredient='Cetirizin'")['rows'], [])
            self.assertEqual(data.query("SELECT SUM(quantity*unit_price)/SUM(quantity) FROM purchase_lines WHERE product_id='P1' AND purchase_unit='box'")['rows'], [[35]])
            self.assertEqual(data.query('SELECT COUNT(*) FROM purchase_lines')['rows'], [[5]])
            self.assertEqual(data.query("SELECT quantity,unit_price,recorded_total,source_status,import_date FROM purchase_lines WHERE import_id='I1' AND line_id='1'")['rows'],
                             [[1,20,19,'Đã nhập hàng','2025-04-02']])
            self.assertEqual(data.query("SELECT quantity,unit_price,recorded_total,import_date FROM purchase_lines WHERE import_id='I2'")['rows'], [[0.5,1000,500,'2025-04-03']])
            self.assertEqual(data.query("SELECT unit_price FROM purchase_lines WHERE import_id='I1' AND product_id='P2'")['rows'], [[None]])
            self.assertEqual(data.query("SELECT active_ingredient FROM products WHERE product_id='P2'")['rows'], [[None]])
            # Mixed units require one factor per product/unit, applied to quantities once.
            mixed = data.query('''SELECT SUM(p.quantity*p.unit_price)/
                SUM(p.quantity*CASE WHEN p.purchase_unit=g.base_unit THEN 1 ELSE u.factor END)*12
                FROM purchase_lines p JOIN products g USING(product_id)
                LEFT JOIN unit_conversions u ON u.product_id=p.product_id AND u.sale_unit=p.purchase_unit
                WHERE p.product_id='P1' ''')['rows'][0][0]
            self.assertAlmostEqual(mixed, 188/72*12)
            source = json.loads(data.query("SELECT lineage FROM purchase_lines WHERE import_id='I1' AND line_id='1'")['rows'][0][0])
            self.assertEqual(source, {'table':'details','file':'details.xlsx','sheet':'Sheet1','row':'2'})
            self.assertEqual(data.query('SELECT SUM(line_revenue) FROM sales')['rows'], [[100]])
            self.assertEqual(data.query('SELECT COUNT(*) FROM inventory_lots')['rows'], [[0]])
            self.assertTrue({'purchase_prices','active_ingredients'} <= set(data.report['source_capabilities']))
            self.assertEqual(raw.path.read_bytes(), before)

    def test_absent_sources_are_not_fabricated(self):
        with TemporaryDirectory() as directory:
            raw = self.fixture(directory)
            raw.tables = [t for t in raw.tables if t['table'] not in {'headers','details','flat','drugs'}]
            data = NormalizedData(raw)
            self.assertEqual(data.query('SELECT COUNT(*) FROM purchase_lines')['rows'], [[0]])
            self.assertNotIn('purchase_prices',data.report['source_capabilities'])
            self.assertNotIn('active_ingredients',data.report['source_capabilities'])

    def test_unknown_or_conflicting_source_values_fail_explicitly(self):
        for extra, message in [([['P1','Different','1']],'Conflicting active ingredient'),
                               ([['UNKNOWN','Drug','1']],'unknown product')]:
            with self.subTest(message=message), TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError,message):
                    NormalizedData(self.fixture(directory,extra))

    def test_unusable_purchase_table_is_reported_absent_and_sales_survive(self):
        with TemporaryDirectory() as directory:
            data = NormalizedData(self.fixture(directory,missing_header=True))
            self.assertEqual(data.report['dropped_sections'], ['purchases'])
            self.assertNotIn('purchase_prices',data.report['source_capabilities'])
            self.assertEqual(data.query('SELECT COUNT(*) FROM purchase_lines')['rows'], [[0]])
            self.assertEqual(data.query('SELECT COUNT(*) FROM order_lines')['rows'], [[1]])


class RejectedRowTests(unittest.TestCase):
    def build(self, directory, bad):
        root = Path(directory)
        columns = ['Mã đơn hàng', 'Ngày đặt hàng', 'Trạng thái đơn hàng', 'Tên sản phẩm', 'Số lượng sản phẩm', 'Tổng tiền',
                   '__dm_file', '__dm_sheet', '__dm_row']
        rows = [[f'O{i}', '01/04/2025', 'lạ' if i < bad else 'xong', 'Hàng', '1', '100', 's.csv', '', str(i)] for i in range(200)]
        with sqlite3.connect(root / 'raw.sqlite') as db:
            db.execute('CREATE TABLE sales (' + ','.join('"' + c + '" TEXT' for c in columns) + ')')
            db.executemany('INSERT INTO sales VALUES (' + ','.join('?' * len(columns)) + ')', rows)
        return NormalizedData(SimpleNamespace(path=root / 'raw.sqlite', cache=root, readme='', tables=[{'table': 'sales'}]))

    def test_few_unreadable_rows_cost_only_those_rows_and_are_reported(self):
        with TemporaryDirectory() as directory:
            data = self.build(directory, 1)
            self.assertEqual(data.query('SELECT COUNT(*) FROM orders')['rows'], [[199]])
            self.assertEqual(data.report['rejected_rows'], {'lines': 1})
            self.assertIn('Unknown order status', data.report['rejected_examples'][0]['error'])

    def test_too_many_unreadable_rows_fail_the_build(self):
        with TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, 'Too many unreadable lines'):
            self.build(directory, 4)


if __name__ == '__main__':
    unittest.main()
