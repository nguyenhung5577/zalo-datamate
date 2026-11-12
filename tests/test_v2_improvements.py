"""Comprehensive unit tests for zalo-dm v2 normalizer, deterministic rules, and dual-verified SQL fallback."""
from datetime import date
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from datamate.books import Books
from datamate.direct import Direct
from datamate.ledgers import Ledgers
from datamate.metrics import Metrics, check_evidence
from datamate.normalized import SCHEMA, parse_debt_note, parse_number, parse_readme_conversions
from datamate.rewrite import kept
from datamate.roles import column_roles
from datamate.sql_fallback import clean_sql, format_sql_result, sql_passes_guards, try_sql_fallback
from scripts.run_metric_layer import answer


class NormalizerV2Tests(unittest.TestCase):
    def test_parse_number_vietnamese_shorthands(self):
        self.assertEqual(parse_number('611 nghìn'), 611000.0)
        self.assertEqual(parse_number('73 ngàn'), 73000.0)
        self.assertEqual(parse_number('1tr229'), 1229000.0)
        self.assertEqual(parse_number('2,5 triệu'), 2500000.0)
        self.assertEqual(parse_number('449.500đ'), 449500.0)

    def test_parse_debt_note_variants(self):
        self.assertEqual(parse_debt_note('nợ 696 nghìn, khách ck đủ rồi', 696000), 0.0)
        self.assertEqual(parse_debt_note('ghi sổ, khách trả hết rồi', 238200), 0.0)
        self.assertEqual(parse_debt_note('nợ cả đơn', 337500), 337500.0)
        self.assertEqual(parse_debt_note('đưa trước 236k, còn lại ghi sổ', 286000), 50000.0)
        self.assertEqual(parse_debt_note('khách đưa trước 87k, còn thiếu 73 nghìn', 160000), 73000.0)
        self.assertEqual(parse_debt_note('trả 259k, nợ 449.500đ', 708500), 449500.0)
        self.assertEqual(parse_debt_note('ck 913 nghìn, thiếu lại 456.500đ', 1369500), 456500.0)
        self.assertEqual(parse_debt_note('ghi sổ 39.668đ', 359000), 39668.0)

    def test_parse_readme_conversions_bullet_and_prose(self):
        readme = (
            '## Quy ước\n'
            '- Ống PVC Bình Minh D32 cây 4m, Ống PVC Bình Minh D50 cây 4m bán theo bó, 1 bó = 10 cây\n'
            '- Bia Tiger lon: 1 thùng = 24 lon\n'
        )
        convs = parse_readme_conversions(readme)
        self.assertIn(('Ống PVC Bình Minh D32 cây 4m', 'bó', 10.0, 'cây'), convs)
        self.assertIn(('Ống PVC Bình Minh D50 cây 4m', 'bó', 10.0, 'cây'), convs)
        self.assertIn(('Bia Tiger lon', 'thùng', 24.0, 'lon'), convs)

    def test_column_roles_import_unit_and_conversion_sheet(self):
        conv_roles = column_roles({'table': 'conv'}, ['Mã vạch', 'Quy cách', 'Bằng'], {})
        self.assertEqual(conv_roles['Mã vạch'], 'product_id')
        self.assertEqual(conv_roles['Quy cách'], 'large_unit')
        self.assertEqual(conv_roles['Bằng'], 'factor')
        imp_roles = column_roles({'table': 'imp'}, ['Ngày nhập', 'Mã phiếu nhập', 'Mã hàng', 'SL nhập', 'ĐVT nhập', 'Giá nhập'], {})
        self.assertEqual(imp_roles['ĐVT nhập'], 'unit')
        line_roles = column_roles({'table': 'lines'}, ['No.', 'Mã bill', 'Còn nợ', 'Bàn', 'Khu vực', 'Số khách'], {})
        self.assertEqual(line_roles['No.'], 'line_no')
        self.assertEqual(line_roles['Còn nợ'], 'debt_left')
        self.assertEqual(line_roles['Bàn'], 'table_name')


class DebtSnapshotAndAliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / 'norm.sqlite'
        self.db = sqlite3.connect(self.db_path)
        for name, ddl in SCHEMA.items():
            self.db.execute(f'CREATE TABLE {name} ({ddl})')
        self.db.execute("""CREATE VIEW sales AS SELECT l.*, o.order_date, o.status, o.customer_id, c.customer_name, c.customer_group,
            o.channel, o.payment_method, p.product_name, p.product_group, p.base_unit, p.prescription, p.is_service
            FROM order_lines l JOIN orders o USING(order_id) JOIN products p USING(product_id) LEFT JOIN customers c USING(customer_id)
            WHERE o.status IN ('completed','shipping')""")
        self.db.execute("""CREATE VIEW daily_revenue AS SELECT order_date, SUM(line_revenue) AS revenue,
            COUNT(DISTINCT order_id) AS order_count FROM sales GROUP BY order_date""")
        self.db.execute("INSERT INTO products VALUES ('p1', 'Sữa bột Meiji số 0 800g', 'Sữa bột', 'lon', 0, 0, NULL)")
        self.db.execute("INSERT INTO product_aliases VALUES ('p1', 'Meiji thanh số 0')")
        self.db.execute("INSERT INTO customers VALUES ('c1', 'Chị Lan', 'lẻ'), ('c2', 'Anh Nam', 'sỉ')")
        self.db.executemany('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)', [
            ('o1', '2026-07-10', None, 'completed', 'c1', 'debt', 500000, '', 'store'),
            ('o2', '2026-07-20', None, 'completed', 'c2', 'debt', 300000, '', 'store'),
            ('o3', '2026-07-22', None, 'cancelled', 'c2', 'debt', 900000, '', 'store'),
        ])
        self.db.executemany('INSERT INTO order_extras VALUES (?,?,?,?,?)', [
            ('o1', 200000, None, None, None),
            ('o2', 300000, None, None, None),
            ('o3', 900000, None, None, None),
        ])
        self.db.executemany('INSERT INTO order_lines VALUES (?,?,?,?,?,?,?,?,?,?)', [
            ('o1', '1', 'p1', 'lon', 1, 1, 500000, 500000, 500000, ''),
            ('o2', '1', 'p1', 'lon', 1, 1, 300000, 300000, 300000, ''),
        ])
        self.db.commit()
        data = SimpleNamespace(
            path=self.db_path,
            report={'source_capabilities': ['line_items', 'customer_identity', 'customer_groups', 'payment_method', 'debt_snapshot']},
            query=lambda sql, params, timeout=10, max_rows=100000: {'rows': [list(r) for r in self.db.execute(sql, params)]},
        )
        self.engine = Metrics(data)
        self.direct = Direct(self.engine)

    def test_debt_snapshot_answers_at_end_of_data_and_rejects_earlier_date(self):
        ans_tot = self.direct.answer({'question': 'Tính đến hết ngày 22/07/2026 khách còn nợ tổng bao nhiêu tiền?', 'as_of_date': '2026-07-22'})
        self.assertEqual(ans_tot, {'type': 'number', 'answer': 500000})
        ans_cnt = self.direct.answer({'question': 'Đến hết ngày 22/07/2026 còn bao nhiêu khách nợ?', 'as_of_date': '2026-07-22'})
        self.assertEqual(ans_cnt, {'type': 'number', 'answer': 2})
        ans_age = self.direct.answer({'question': 'Đến hết ngày 22/07/2026 nợ chia theo tuổi nợ 0-7 ngày, 8-15 ngày, trên 15 ngày mỗi nhóm bao nhiêu?', 'as_of_date': '2026-07-22'})
        self.assertEqual(ans_age, {'type': 'list', 'answer': [300000, 200000, 0]})
        ans_old = self.direct.answer({'question': 'Tính đến hết ngày 12/07/2026 khách còn nợ tổng bao nhiêu tiền?', 'as_of_date': '2026-07-22'})
        self.assertEqual(ans_old['type'], 'unanswerable')

    def test_product_alias_resolves_in_direct_and_metrics(self):
        ans = self.direct.answer({'question': 'Tháng 7/2026 bán được bao nhiêu lon Meiji thanh số 0?', 'as_of_date': '2026-07-22'})
        self.assertEqual(ans, {'type': 'number', 'answer': 2})
        filters = {'product': 'Meiji thanh so 0'}
        self.engine.check_filters(filters, 'thang 7/2026 doanh thu meiji thanh so 0')
        self.assertEqual(filters['product'], 'Sữa bột Meiji số 0 800g')


class SqlFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db_path = Path(self.tmp.name) / 'fb.sqlite'
        db = sqlite3.connect(db_path)
        for name, ddl in SCHEMA.items():
            db.execute(f'CREATE TABLE {name} ({ddl})')
        db.execute("""CREATE VIEW sales AS SELECT l.*, o.order_date, o.status, o.customer_id, c.customer_name, c.customer_group,
            o.channel, o.payment_method, p.product_name, p.product_group, p.base_unit, p.prescription, p.is_service
            FROM order_lines l JOIN orders o USING(order_id) JOIN products p USING(product_id) LEFT JOIN customers c USING(customer_id)
            WHERE o.status IN ('completed','shipping')""")
        db.execute("""CREATE VIEW daily_revenue AS SELECT order_date, SUM(line_revenue) AS revenue,
            COUNT(DISTINCT order_id) AS order_count FROM sales GROUP BY order_date""")
        db.execute("INSERT INTO products VALUES ('p1', 'Cà phê đen', 'Cà phê', 'ly', 0, 0, NULL)")
        db.execute("INSERT INTO orders VALUES ('o1', '2026-03-10', '2026-03-10 09:00', 'completed', NULL, 'cash', 50000, '', 'store')")
        db.execute("INSERT INTO order_extras VALUES ('o1', 0, 'Bàn 02', 'Sân vườn', 4)")
        db.execute("INSERT INTO order_lines VALUES ('o1', '1', 'p1', 'ly', 2, 2, 25000, 25000, 50000, '')")
        db.commit()
        db.close()
        data = SimpleNamespace(
            path=db_path,
            report={'source_capabilities': ['line_items', 'tables', 'guest_count']},
            query=lambda sql, params, timeout=10, max_rows=100000: {'rows': [list(r) for r in sqlite3.connect(db_path).execute(sql, params)]},
        )
        self.engine = Metrics(data)

    def test_sql_guards_block_mutations_and_unfiltered_orders(self):
        self.assertIsNone(clean_sql('DROP TABLE orders'))
        self.assertIsNone(clean_sql('SELECT 1; SELECT 2'))
        self.assertFalse(sql_passes_guards('SELECT SUM(invoice_total) FROM orders', 'doanh thu thang 3'))
        self.assertTrue(sql_passes_guards('SELECT SUM(line_revenue) FROM sales', 'doanh thu thang 3'))

    def test_sql_zero_results_are_not_answers(self):
        # COUNT over a filter that matched nothing gives 0; that is far more often a wrong filter than a true zero.
        self.assertIsNone(format_sql_result({'answer_type': 'number'}, ['n'], [(0,)]))
        self.assertIsNone(format_sql_result({'answer_type': 'percent'}, ['p'], [(0.0,)]))
        self.assertEqual(format_sql_result({'answer_type': 'number'}, ['n'], [(7,)]), {'type': 'number', 'answer': 7})
        self.assertEqual(format_sql_result({'answer_type': 'string'}, ['m'], [('2025-05',)]), {'type': 'date', 'answer': '2025-05'})
        self.assertIsNone(format_sql_result({'answer_type': 'table'}, ['name', 'n'], [('Latte (L)', 65), ('Trà vải (L)', 83)]))

    def test_dual_verification_emits_when_agreed_and_abstains_when_disagreed(self):
        q = {'id': 'q1', 'question': 'Tháng 3/2026 khu vực Sân vườn phục vụ tổng bao nhiêu lượt khách ngồi?', 'as_of_date': '2026-03-31'}
        agreed_reply = '{"abstain":false,"answer_type":"number","is_money":false,"sql":"SELECT SUM(e.guest_count) FROM order_extras e JOIN orders o USING(order_id) WHERE o.status IN (\'completed\',\'shipping\') AND e.area = \'Sân vườn\'"}'
        ans, meta = try_sql_fallback(q, q['question'], self.engine, SimpleNamespace(chat=lambda *a, **k: (agreed_reply, {}, 'stop')))
        self.assertEqual(ans, {'type': 'number', 'answer': 4})
        self.assertTrue(meta.get('verified'))

        calls = []
        replies = [
            '{"abstain":false,"answer_type":"number","is_money":false,"sql":"SELECT 4"}',
            '{"abstain":false,"answer_type":"number","is_money":false,"sql":"SELECT 5"}',
        ]
        def disagree_chat(*a, **k):
            calls.append(1)
            return replies[len(calls) - 1], {}, 'stop'
        ans2, meta2 = try_sql_fallback(q, q['question'], self.engine, SimpleNamespace(chat=disagree_chat))
        self.assertIsNone(ans2)
        self.assertFalse(meta2.get('verified'))


    def test_verifier_model_writes_prompt_b(self):
        q = {'id': 'q1', 'question': 'Tháng 3/2026 khu vực Sân vườn phục vụ tổng bao nhiêu lượt khách ngồi?', 'as_of_date': '2026-03-31'}
        reply = '{"abstain":false,"answer_type":"number","is_money":false,"sql":"SELECT 4"}'
        main, second = [], []
        main_model = SimpleNamespace(chat=lambda *a, **k: (main.append(k), (reply, {}, 'stop'))[1])
        other = SimpleNamespace(name='other.gguf', chat=lambda *a, **k: (second.append(k), (reply, {}, 'stop'))[1])
        ans, meta = try_sql_fallback(q, q['question'], self.engine, main_model, verifier=other)
        self.assertEqual(ans, {'type': 'number', 'answer': 4})
        self.assertEqual((len(main), len(second)), (1, 1))
        self.assertEqual(second[0]['temperature'], 0)
        self.assertEqual(meta['sql_fallback_b']['model'], 'other.gguf')


class RewriteGuardTests(unittest.TestCase):
    def test_added_metric_needs_shorthand_in_the_original(self):
        self.assertFalse(kept('dt tháng 7/2025 bn?', 'Đơn hàng tháng 7/2025 bao nhiêu?'))
        self.assertTrue(kept('dt tháng 7/2025 bn?', 'Doanh thu tháng 7/2025 là bao nhiêu?'))
        self.assertTrue(kept('hđ thanh toán tm tuần rồi mấy cái', 'Tuần rồi có mấy hóa đơn thanh toán tiền mặt?'))

    def test_whole_and_loose_units_are_kept(self):
        self.assertFalse(kept('ton kho strepsils hop 2 vi x 12 vien hom nay dc may hop chan va may vien le',
                              'Tồn kho Strepsils hộp 2 vỉ x 12 viên hôm nay được mấy hộp và mấy viên lẻ?'))

    def test_dt_is_not_read_as_a_phone_channel(self):
        self.assertFalse(kept('hqua dt duoc bao nhieu?', 'Hôm qua điện thoại được bao nhiêu?'))
        self.assertTrue(kept('dt hqua cua cua hang la bn', 'Doanh thu hôm qua của cửa hàng là bao nhiêu?'))
        self.assertTrue(kept('sl Pepsi bán qua điện thoại tháng này', 'Số lượng Pepsi bán qua điện thoại tháng này là bao nhiêu?'))

    def test_money_needs_a_money_word_in_the_original(self):
        self.assertFalse(kept('so bill hom nay la bao nhieu', 'Số tiền hóa đơn hôm nay là bao nhiêu?'))
        self.assertTrue(kept('tuần rồi tiệm bán đc bnhiu tieenf', 'Tuần rồi tiệm bán được bao nhiêu tiền?'))
        self.assertTrue(kept('hđ thanh toán tm tuần rồi mấy cái', 'Tuần rồi có mấy hóa đơn thanh toán tiền mặt?'))


class RuleScopeTests(unittest.TestCase):
    def setUp(self):
        import json
        import os
        from datamate import provenance
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / 'rule_provenance.json'
        path.write_text(json.dumps({'min_train_hits': 5, 'rules': {'books.repair': {'used': 0}, 'books.phones': {'used': 4},
                                                                     'books.sizes': {'used': 5}}}))
        old_path, old_scope = provenance.PROVENANCE, os.environ.get('DATAMATE_RULES')
        provenance.PROVENANCE = path

        def restore():
            provenance.PROVENANCE = old_path
            if old_scope is None:
                os.environ.pop('DATAMATE_RULES', None)
            else:
                os.environ['DATAMATE_RULES'] = old_scope
        self.addCleanup(restore)
        self.provenance, self.env = provenance, os.environ

    def test_all_scope_keeps_every_rule(self):
        self.env['DATAMATE_RULES'] = 'all'
        self.assertEqual(self.provenance.unbacked('books'), [])

    def test_train_scope_switches_off_rules_below_the_threshold(self):
        self.env['DATAMATE_RULES'] = 'train'
        off = set(self.provenance.unbacked('books'))
        self.assertIn('repair', off)
        self.assertIn('phones', off)
        self.assertNotIn('sizes', off)
        # A rule the census never saw has no training support either.
        self.assertIn('new_members', off)
        shelf = SimpleNamespace(**{name: (lambda *a, **k: {'type': 'number', 'answer': 1}) for name in self.provenance.LAYERS['books']})
        self.provenance.switch_off(shelf, 'books')
        # An unbacked rule that would answer withholds the question instead of letting a later rule answer it.
        with self.assertRaises(self.provenance.Withheld):
            shelf.repair('x', '2026-01-01')
        quiet = SimpleNamespace(repair=lambda *a, **k: None)
        self.provenance.switch_off(quiet, 'books')
        self.assertIsNone(quiet.repair('x', '2026-01-01'))
        self.assertEqual(shelf.sizes('x', '2026-01-01'), {'type': 'number', 'answer': 1})

    def test_unknown_scope_is_an_error(self):
        self.env['DATAMATE_RULES'] = 'public'
        with self.assertRaises(ValueError):
            self.provenance.scope()

class PhoneBooksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        norm_path = Path(self.tmp.name) / 'norm.sqlite'
        raw_path = Path(self.tmp.name) / 'raw.sqlite'

        ndb = sqlite3.connect(norm_path)
        for name, ddl in SCHEMA.items():
            ndb.execute(f'CREATE TABLE {name} ({ddl})')
        ndb.execute("""CREATE VIEW sales AS SELECT l.*, o.order_date, o.status, o.customer_id, c.customer_name, c.customer_group,
            o.channel, o.payment_method, p.product_name, p.product_group, p.base_unit, p.prescription, p.is_service
            FROM order_lines l JOIN orders o USING(order_id) JOIN products p USING(product_id) LEFT JOIN customers c USING(customer_id)
            WHERE o.status IN ('completed','shipping')""")
        ndb.execute("""CREATE VIEW daily_revenue AS SELECT order_date, SUM(line_revenue) AS revenue,
            COUNT(DISTINCT order_id) AS order_count FROM sales GROUP BY order_date""")
        ndb.executemany('INSERT INTO products VALUES (?,?,?,?,?,?,?)', [
            ('p1', 'iPhone 15 128GB', None, 'cái', 0, 0, None),
            ('p2', 'iPhone 15 256GB', None, 'cái', 0, 0, None),
            ('p3', 'Samsung S23 128GB (cũ)', None, 'cái', 0, 0, None),
            ('p4', 'Ốp lưng iPhone', None, 'cái', 0, 0, None),
        ])
        ndb.executemany('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)', [
            ('1', '2026-05-05', None, 'completed', None, 'debt', 40000000, '', 'store'),
            ('2', '2026-05-10', None, 'completed', None, 'cash', 11000000, '', 'store'),
            ('70', '2026-05-12', None, 'cancelled', None, 'debt', 15000000, '', 'store'),
        ])
        ndb.executemany('INSERT INTO order_lines VALUES (?,?,?,?,?,?,?,?,?,?)', [
            ('1', '1', 'p1', 'cái', 1, 1, 20000000, 20000000, 20000000, ''),
            ('1', '2', 'p2', 'cái', 1, 1, 20000000, 20000000, 20000000, ''),
            ('2', '1', 'p3', 'cái', 1, 1, 10000000, 10000000, 10000000, ''),
            ('2', '2', 'p4', 'cái', 10, 10, 100000, 100000, 1000000, ''),
        ])
        ndb.commit()
        ndb.close()

        rdb = sqlite3.connect(raw_path)
        rdb.execute('CREATE TABLE ghi_tat ("Ghi tắt" TEXT, "Tên hàng" TEXT, "Loại" TEXT)')
        rdb.executemany('INSERT INTO ghi_tat VALUES (?,?,?)', [
            ('ip15-128', 'iPhone 15 128GB', 'Máy mới'),
            ('ip15-256', 'iPhone 15 256GB', 'Máy mới'),
            ('s23-cu', 'Samsung S23 128GB (cũ)', 'Máy cũ'),
            ('op', 'Ốp lưng iPhone', 'Phụ kiện'),
        ])
        rdb.execute('CREATE TABLE so_no ("Ngày" TEXT, "Khách" TEXT, "Nợ" REAL, "Đã trả" REAL, "Ngày trả" TEXT, "Ghi chú" TEXT)')
        rdb.executemany('INSERT INTO so_no VALUES (?,?,?,?,?,?)', [
            ('2026-05-05', 'Anh A', 10000000, 3000000, '2026-05-10', 'STT 1'),
            (None, None, None, 2000000, '2026-05-15', None),
            ('2026-05-12', 'Anh B', 15000000, None, None, 'STT 70'),
        ])
        rdb.execute('CREATE TABLE tra_gop ("Khách" TEXT, "Ngày mua" TEXT, "Giá máy" REAL, "Trả trước" REAL, "Số kỳ" INTEGER, "Góp/tháng" REAL, "Ngày đóng" INTEGER, "Đã đóng" TEXT)')
        rdb.execute("INSERT INTO tra_gop VALUES ('Anh C', '2026-01-01', 5000000, 5000000, 1, 0, 1, NULL)")
        rdb.commit()
        rdb.close()

        data = SimpleNamespace(
            path=norm_path,
            raw=SimpleNamespace(path=raw_path),
            report={'source_capabilities': ['line_items']},
            query=lambda sql, params, timeout=10, max_rows=100000: {'rows': [list(r) for r in sqlite3.connect(norm_path).execute(sql, params)]},
        )
        self.direct = Direct(Metrics(data))

    def test_so_no_continuation_repayments_and_cancelled_order_exclusion(self):
        ans = self.direct.answer({'question': 'Tính đến hết ngày 20/05/2026 khách còn nợ tổng bao nhiêu tiền?', 'as_of_date': '2026-05-31'})
        self.assertEqual(ans, {'type': 'number', 'answer': 5000000})

    def test_top_dong_may_excludes_accessories_and_groups_capacities(self):
        ans = self.direct.answer({'question': 'Tháng 5/2026 top 2 dòng máy bán chạy nhất theo số lượng là gì?', 'as_of_date': '2026-05-31'})
        self.assertEqual(ans, {'type': 'list', 'answer': ['iPhone 15', 'Samsung S23']})
        share = self.direct.answer({'question': 'Tháng 5/2026 doanh thu máy mới chiếm bao nhiêu % doanh thu máy?', 'as_of_date': '2026-05-31'})
        self.assertEqual(share, {'type': 'percent', 'answer': 80.0})


if __name__ == '__main__':
    unittest.main()
