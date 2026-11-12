from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from datamate.metrics import Metrics, Unsupported, check_evidence, previous_period, resolve_period
from datamate.normalized import SCHEMA
from scripts.run_metric_layer import answer


class FakeData:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        for name, ddl in SCHEMA.items():
            self.db.execute('CREATE TABLE ' + name + ' (' + ddl + ')')
        self.db.execute("""CREATE VIEW sales AS SELECT l.*, o.order_date, o.status, o.customer_id, c.customer_name, c.customer_group,
            o.channel, o.payment_method, p.product_name, p.product_group, p.base_unit, p.prescription, p.is_service
            FROM order_lines l JOIN orders o USING(order_id) JOIN products p USING(product_id) LEFT JOIN customers c USING(customer_id)
            WHERE o.status IN ('completed','shipping')""")
        self.db.execute("""CREATE VIEW daily_revenue AS SELECT order_date, SUM(line_revenue) AS revenue,
            COUNT(DISTINCT order_id) AS order_count FROM sales GROUP BY order_date""")
        self.db.executemany('INSERT INTO products VALUES (?,?,?,?,?,?,?)', [('p1', 'Trà', 'Nước', 'ly', 0, 0, None), ('p2', 'Phí phục vụ', None, None, 0, 1, None)])
        self.db.execute("INSERT INTO customers VALUES ('k1', 'An', 'lẻ')")
        self.db.executemany('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)', [
            ('o1', '2025-03-01', '2025-03-01 07:00', 'completed', 'k1', 'debt', 100, '', 'store'),
            ('o2', '2025-03-02', '2025-03-02 15:00', 'returned', None, 'cash', 50, '', 'store'),
            ('o3', '2025-03-03', '2025-03-03 15:00', 'cancelled', None, 'cash', 70, '', 'phone'),
            ('o4', '2025-02-10', '2025-02-10 15:00', 'shipping', None, 'cod', 40, '', 'phone')])
        self.db.executemany('INSERT INTO order_lines VALUES (?,?,?,?,?,?,?,?,?,?)', [
            ('o1', '1', 'p1', 'ly', 2, 2, 40, 40, 80, ''), ('o1', '2', 'p2', None, 1, 1, 20, 20, 20, ''),
            ('o2', '1', 'p1', 'ly', 1, 1, 50, 50, 50, ''), ('o4', '1', 'p1', 'ly', 1, 1, 40, 40, 40, '')])
        self.db.executemany('INSERT INTO payments VALUES (?,?,?,?,?)', [('a', 'o1', '2025-03-01', 30, ''), ('b', 'o1', '2025-03-05', 20, ''), ('c', 'o2', '2025-03-02', 50, '')])
        self.report = {'source_capabilities': ['line_items', 'customer_identity', 'payments', 'payment_method']}

    def query(self, sql, params, timeout, max_rows):
        return {'rows': [list(row) for row in self.db.execute(sql, params)]}


class PeriodTests(unittest.TestCase):
    def test_periods_follow_the_rulebook(self):
        self.assertEqual(resolve_period({'kind': 'last_week'}, '2025-03-05'), (date(2025, 2, 24), date(2025, 3, 2)))
        self.assertEqual(resolve_period({'kind': 'this_week'}, '2025-03-05'), (date(2025, 3, 3), date(2025, 3, 5)))
        self.assertEqual(resolve_period({'kind': 'last_days', 'days': 30}, '2025-03-30'), (date(2025, 3, 1), date(2025, 3, 30)))
        self.assertEqual(resolve_period({'kind': 'month', 'month': 11}, '2025-03-05'), (date(2024, 11, 1), date(2024, 11, 30)))

    def test_this_month_compares_with_the_same_days_capped_at_month_end(self):
        start, end = resolve_period({'kind': 'this_month'}, '2025-03-31')
        self.assertEqual(previous_period({'kind': 'this_month'}, start, end), (date(2025, 2, 1), date(2025, 2, 28)))

    def test_same_month_last_year(self):
        start, end = resolve_period({'kind': 'month', 'month': 2, 'year': 2025}, '2025-03-05')
        self.assertEqual(previous_period({'kind': 'month'}, start, end, 'last_year'), (date(2024, 2, 1), date(2024, 2, 29)))


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = Metrics(FakeData(Path(self.directory.name) / 'x.sqlite'))

    def compute(self, metric, period=None, **slots):
        return self.engine.compute({'metric': metric, 'period': period or {'kind': 'month', 'month': 3, 'year': 2025}, **slots}, '2025-03-31')

    def test_revenue_drops_whole_returned_and_cancelled_orders_and_keeps_fees(self):
        self.assertEqual(self.compute('revenue')['answer'], 100)
        self.assertEqual(self.compute('orders')['answer'], 1)

    def test_cancel_rate_counts_every_order_placed(self):
        self.assertEqual(self.compute('cancel_rate'), {'type': 'percent', 'answer': 33.33})

    def test_debt_uses_invoice_total_and_only_later_payments_count_as_collected(self):
        self.assertEqual(self.compute('debt_collected')['answer'], 20)
        self.assertEqual(self.compute('debt_total', {'kind': 'until', 'date': '2025-03-04'})['answer'], 70)
        self.assertEqual(self.compute('debt_total', {'kind': 'until', 'date': '2025-03-31'})['answer'], 50)
        self.assertEqual(self.compute('debtors', {'kind': 'until', 'date': '2025-03-31'})['answer'], 1)

    def test_ranking_skips_service_lines(self):
        self.assertEqual(self.compute('top_products', top=2), {'type': 'list', 'answer': ['Trà']})

    def test_widened_metrics_follow_the_rules_definitions(self):
        self.assertEqual(self.compute('units', filters={'product': 'Trà'}, unit='ly'), {'type': 'number', 'answer': 2})
        with self.assertRaises(Unsupported):
            self.compute('units', filters={'product': 'Trà'}, unit='thung')
        self.assertEqual(self.compute('best_day'), {'type': 'date', 'answer': '2025-03-01'})
        self.assertEqual(self.compute('count_over', count='orders', min=100)['answer'], 1)
        self.assertEqual(self.compute('count_over', count='orders', min=101)['answer'], 0)
        later = {'kind': 'range', 'start': '2025-03-02', 'end': '2025-03-05'}
        self.assertEqual(self.compute('unsold', later), {'type': 'number', 'answer': 2})
        with self.assertRaises(Unsupported):
            self.compute('best_day', filters={'channel': 'store'})
        self.engine.capabilities.discard('customer_identity')
        self.assertEqual(self.compute('count_over', count='customers', min=1)['type'], 'unanswerable')
        self.assertEqual(self.compute('top_customers')['type'], 'unanswerable')

    def test_widened_evidence_reads_amounts_units_and_dates_from_the_question(self):
        month = {'kind': 'month', 'month': 1, 'year': 2025}
        slots = {'metric': 'count_over', 'count': 'orders', 'period': month}
        check_evidence(slots, 'tháng 3/2025 có mấy đơn có dthu ≥ 1.500.000 đồng')
        self.assertEqual(slots['min'], 1500000)
        slots = {'metric': 'units', 'period': month, 'filters': {'product': 'Trà sữa (L)'}}
        check_evidence(slots, 'Tháng 1/2025 bán được mấy ly trà sữa (L)?')
        self.assertEqual(slots['unit'], 'ly')
        slots = {'metric': 'unsold', 'period': month}
        check_evidence(slots, 'Từ 20/10/2025 đến hết 26/10/2025 có mấy mặt hàng bị ế?')
        self.assertEqual(slots['period'], {'kind': 'range', 'start': '2025-10-20', 'end': '2025-10-26'})
        for slots, text in [({'metric': 'best_day', 'period': month}, 'tháng nào doanh thu cao nhất'),
                            ({'metric': 'best_day', 'period': month}, 'ngày nào doanh thu thấp nhất tháng 1/2025'),
                            ({'metric': 'count_over', 'count': 'items', 'period': month}, 'tháng 1/2025 món có từ 3 đơn trở lên chiếm bao nhiêu %'),
                            ({'metric': 'count_over', 'count': 'days', 'period': month}, 'tháng 1/2025 có mấy đơn từ 500.000 đồng trở lên'),
                            ({'metric': 'units', 'period': month}, 'tháng 1/2025 bán mấy ly')]:
            with self.assertRaises(Unsupported, msg=text):
                check_evidence(slots, text)

    def test_second_widening_follows_the_rules_definitions(self):
        self.assertEqual(self.compute('price', {'kind': 'day', 'date': '2025-03-01'}, filters={'product': 'tra'}), {'type': 'number', 'answer': 40})
        self.assertEqual(self.compute('top_debtors', {'kind': 'until', 'date': '2025-03-04'}, top=1),
                         {'type': 'table', 'answer': {'columns': ['khach_hang', 'so_tien_no'], 'rows': [['An', 70]]}})
        self.assertEqual(self.compute('top_customers', of='orders'), {'type': 'string', 'answer': 'An'})
        month = {'kind': 'month', 'month': 1, 'year': 2025}
        for metric, text, answer in [('monthly', 'doanh thu thang 2/2025 va thang 3/2025 lan luot la bao nhieu', [40, 100]),
                                     ('rising', 'dthu thang 2/2025, thang 3/2025 co tang lien tuc khong', True),
                                     ('best_month', 'thang 2/2025 va thang 3/2025, thang nao doanh thu cao nhat', '2025-03')]:
            slots = {'metric': metric, 'period': dict(month)}
            check_evidence(slots, text)
            self.assertEqual(self.engine.compute(slots, '2025-03-31')['answer'], answer, text)
        slots = {'metric': 'top_debtors', 'period': dict(month)}
        check_evidence(slots, 'liet ke 3 khach no nhieu nhat tinh den het ngay 04/03/2025')
        self.assertEqual((slots['top'], slots['period']), (3, {'kind': 'until', 'date': '2025-03-04'}))
        for slots, text in [({'metric': 'monthly', 'period': month}, 'doanh thu thang 2/2025 hon thang 3/2025 bao nhieu'),
                            ({'metric': 'price', 'period': month, 'filters': {'product': 'Trà'}}, 'gia nhap tra ngay 01/03/2025'),
                            ({'metric': 'best_month', 'period': month}, 'thang 2/2025 va thang 3/2025, thang nao doanh thu thap nhat')]:
            with self.assertRaises(Unsupported, msg=text):
                check_evidence(slots, text)

    def test_third_widening_follows_the_rules_definitions(self):
        month = {'kind': 'month', 'month': 3, 'year': 2025}
        cases = [({'metric': 'revenue', 'filters': {'payment': ['cash', 'debt']}}, 'tháng 3/2025 doanh thu đơn ghi nợ và đơn tiền mặt lần lượt là bao nhiêu',
                  {'type': 'list', 'answer': [100, 0]}),
                 ({'metric': 'customer_debt', 'filters': {'customer_group': 'An'}}, 'tháng 3/2025 khách An nợ thêm bao nhiêu, trả được bao nhiêu',
                  {'type': 'list', 'answer': [70, 20]}),
                 ({'metric': 'share', 'filters': {'count_over': {'count': 'orders', 'threshold': 1}}}, 'tháng 3/2025 bao nhiêu phần trăm khách mua từ 1 đơn trở lên',
                  {'type': 'percent', 'answer': 100.0}),
                 ({'metric': 'price_effect', 'filters': {'product': 'tra'}}, 'trà lên giá 40.000đ từ ngày 01/03/2025; so 30 ngày kể từ ngày tăng giá với 30 ngày ngay trước, tăng hay giảm bao nhiêu %',
                  {'type': 'percent', 'answer': 100.0})]
        for slots, text, answer in cases:
            slots['period'] = dict(month)
            check_evidence(slots, text)
            self.assertEqual(self.engine.compute(slots, '2025-03-31'), answer, text)
        slots = {'metric': 'share', 'period': dict(month), 'filters': {'payment': ['cash']}}
        check_evidence(slots, 'tháng 3/2025 đơn tiền mặt chiếm bao nhiêu phần trăm doanh thu')
        self.assertEqual(slots['filters'], {'payment': 'cash'})
        for slots, text in [({'metric': 'revenue', 'filters': {'payment': ['cash', 'debt']}}, 'tháng 3/2025 doanh thu đơn tiền mặt bao nhiêu'),
                            ({'metric': 'revenue', 'filters': {'customer': 'An'}}, 'tháng 3/2025 doanh thu khách An bao nhiêu'),
                            ({'metric': 'customer_debt', 'filters': {'customer': 'An'}}, 'tháng 3/2025 khách An còn nợ bao nhiêu'),
                            ({'metric': 'price_effect', 'filters': {'product': 'tra'}}, 'trà lên giá 40.000đ; so 30 ngày sau với 30 ngày trước bao nhiêu %')]:
            slots['period'] = dict(month)
            with self.assertRaises(Unsupported, msg=text):
                check_evidence(slots, text)
                self.engine.compute(slots, '2025-03-31')

    def test_stock_and_gross_profit_use_the_ledger_rules(self):
        slots = {'metric': 'stock_left', 'filters': {'product': 'tra'}}
        check_evidence(slots, 'cuối ngày hôm nay trà còn bao nhiêu hộp chẵn, bao nhiêu gói lẻ')
        self.assertEqual(slots['period'], {'kind': 'today'})
        profit = {'month': {'kind': 'month', 'month': 3, 'year': 2025}}
        best = {'metric': 'gross_profit'}
        check_evidence(best, 'tháng 3/2025 hàng nào lời nhất')
        total = {'metric': 'gross_profit'}
        check_evidence(total, 'lãi gộp tháng 3/2025 là bao nhiêu')
        self.assertEqual((best['item'], total['item'], total['period']), (True, False, profit['month']))
        self.engine.direct = SimpleNamespace(open_books=lambda: None, products=[(1, 'tra', 'goi', ['tra'])],
                                             ledgers=SimpleNamespace(profit=lambda span: {'tra': 30, 'ca phe': 50},
                                                                     stock_left=lambda plain, as_of, product: {'type': 'list', 'answer': [2, 3]}))
        self.assertEqual(self.engine.compute(best, '2025-03-31'), {'type': 'string', 'answer': 'ca phe'})
        self.assertEqual(self.engine.compute(total, '2025-03-31'), {'type': 'number', 'answer': 80})
        self.assertEqual(self.engine.metric_stock_left(slots, None, None, '2025-03-31'), {'type': 'list', 'answer': [2, 3]})
        for slots, text in [({'metric': 'gross_profit'}, 'lãi ròng tháng 3/2025 là bao nhiêu'),
                            ({'metric': 'gross_profit'}, 'tháng 3/2025 hàng nào có lãi gộp'),
                            ({'metric': 'gross_profit'}, 'tháng 3/2025 lãi gộp chiếm mấy phần trăm doanh thu'),
                            ({'metric': 'stock_left', 'filters': {'product': 'tra'}}, 'hôm nay trà bán được bao nhiêu gói'),
                            ({'metric': 'stock_left', 'filters': {'product': 'tra'}}, 'trà trong kho còn bao nhiêu gói')]:
            with self.assertRaises(Unsupported, msg=text):
                check_evidence(slots, text)

    def test_staff_supplier_units_and_debt_age_read_their_conditions_from_the_words(self):
        march = {'kind': 'month', 'month': 3, 'year': 2025}
        best = {'metric': 'staff_sales'}
        check_evidence(best, 'nhân viên bán giỏi nhất tháng 3/2025 là ai')
        one = {'metric': 'staff_sales'}
        check_evidence(one, 'tháng 3/2025 nhân viên mã NV002 bán được bao nhiêu tiền')
        supplier = {'metric': 'top_supplier'}
        check_evidence(supplier, 'tháng 3/2025 nhập nhiều tiền nhất từ nhà phân phối nào')
        units = {'metric': 'units_total'}
        check_evidence(units, 'tháng 3/2025 quán bán được bao nhiêu ly size M')
        bands = {'metric': 'debt_age'}
        check_evidence(bands, 'đến hết ngày 04/12/2025 số tiền khách còn nợ ở nhóm tuổi nợ 0–7, 8–15 và trên 15 ngày lần lượt là bao nhiêu')
        self.assertEqual((best['item'], one['item'], supplier['main'], best['period'], units['period']), (True, False, False, march, march))
        self.assertEqual(bands['period'], {'kind': 'until', 'date': '2025-12-04'})
        self.engine.direct = SimpleNamespace(open_books=lambda: None, ledgers=SimpleNamespace(
            staff_sales=lambda plain, span, item: {'type': 'string', 'answer': 'An'} if item else None, top_supplier=lambda span, main: None))
        self.assertEqual(self.engine.compute(best, '2025-03-31'), {'type': 'string', 'answer': 'An'})
        with self.assertRaises(Unsupported):
            self.engine.compute(one, '2025-03-31')
        for slots, text in [({'metric': 'staff_sales'}, 'tháng 3/2025 nhân viên nào bán được nhiều đơn nhất'),
                            ({'metric': 'staff_sales'}, 'tháng 3/2025 nhân viên nào bán ít tiền nhất'),
                            ({'metric': 'top_supplier'}, 'tháng 3/2025 nhập từ bao nhiêu nhà cung cấp'),
                            ({'metric': 'units_total', 'filters': {'product': 'Trà'}}, 'tháng 3/2025 bán được bao nhiêu ly trà'),
                            ({'metric': 'debt_age'}, 'khách còn nợ ở các đơn quá 15 ngày là bao nhiêu')]:
            with self.assertRaises(Unsupported, msg=text):
                check_evidence(slots, text)

    def test_a_month_named_without_its_year_overrides_the_models_period(self):
        from datamate.metrics import fix_period
        slots = {'metric': 'revenue', 'period': {'kind': 'last_month'}}
        fix_period(slots, 'lai gop thang 6 duoc bao nhieu')
        self.assertEqual(slots['period'], {'kind': 'month', 'month': 6})
        slots = {'metric': 'revenue', 'period': {'kind': 'last_month'}}
        fix_period(slots, 'doanh thu thang 12 nam ngoai')
        self.assertEqual(slots['period'], {'kind': 'last_month'})
        for text in ('ngay 5 thang 6 doanh thu', 'thang 5 va thang 6 doanh thu'):
            with self.assertRaises(Unsupported, msg=text):
                fix_period({'metric': 'revenue', 'period': {'kind': 'last_month'}}, text)

    def test_a_shortened_product_name_finds_the_one_whole_name_in_the_question(self):
        self.engine.rows = lambda sql, params=(): [['Má heo nướng'], ['Vú heo nướng'], ['Trà']] if 'product_name FROM products' in sql and 'WHERE' not in sql else []
        filters = {'product': 'heo nướng'}
        self.engine.check_filters(filters, 'thang 3/2025 ban ma heo nuong duoc bao nhieu tien')
        self.assertEqual(filters, {'product': 'Má heo nướng'})
        with self.assertRaises(Unsupported):
            self.engine.check_filters({'product': 'heo nướng'}, 'thang 3/2025 ban heo nuong duoc bao nhieu tien')

    def test_rulebook_definitions_the_form_cannot_express_leave_the_layer(self):
        month = {'kind': 'month', 'month': 3, 'year': 2025}
        for metric, text in [('top_customers', 'Tháng 3/2025, trong các khách hàng, ai trung thành nhất?'),
                             ('top_products', 'tháng 3/2025, hàng nào được khách ưa chuộng nhất?'),
                             ('average_order', 'Phiếu có thu tiền tháng 3/2025 trung bình 1 phiếu bao nhiêu tiền?'),
                             ('revenue', 'tháng 3/2025 tổng tiền khách phải trả là bao nhiêu?'),
                             ('cancelled', 'tháng 3/2025 có mấy đơn khách trả lại vì gọi nhầm món?'),
                             ('revenue', 'tháng 3/2025 đơn bán trên sàn với đơn bán qua Zalo, từng bên dthu bao nhiêu'),
                             ('growth', 'dthu từ mùng 1 tới hết hôm nay so với cùng kỳ tháng trước cao hơn bao nhiêu %')]:
            with self.assertRaises(Unsupported, msg=text):
                check_evidence({'metric': metric, 'period': dict(month)}, text)
        # "Sốp" alone is chat for the shop, not the big-spender ranking.
        check_evidence({'metric': 'revenue', 'period': dict(month)}, 'Trong tháng 3/2025, sốp có doanh thu bao nhiêu tiền?')

    def test_period_outside_data_is_unanswerable(self):
        self.assertEqual(self.compute('revenue', {'kind': 'month', 'month': 3, 'year': 2020})['type'], 'unanswerable')

    def test_invented_filter_and_other_metric_leave_the_layer(self):
        with self.assertRaises(Unsupported):
            self.compute('revenue', filters={'channel': 'shopee'})
        with self.assertRaises(Unsupported):
            self.engine.compute({'metric': 'other'}, '2025-03-31')

    def test_evidence_rejects_slots_the_question_does_not_support(self):
        month = {'kind': 'month', 'month': 1, 'year': 2025}
        for slots, text in [({'metric': 'revenue', 'top': 1, 'period': month}, 'revenue ngày nào'),
                            ({'metric': 'orders', 'period': month}, 'thang 5/2025 bi huy may don'),
                            ({'metric': 'revenue', 'period': month, 'filters': {'product_group': 'dùng ngoài'}}, 'dthu hàng không kê đơn'),
                            ({'metric': 'orders', 'period': month, 'filters': {'shift': 'morning'}}, 'tháng 1 mấy đơn'),
                            ({'metric': 'revenue', 'period': month}, 'điện thoại là bao nhiêu, tại quầy là bao nhiêu')]:
            with self.assertRaises(Unsupported, msg=text):
                check_evidence(slots, text)
        kept = {'metric': 'revenue', 'period': month, 'filters': {'customer_group': 'thân thiết'}}
        check_evidence(kept, 'dthu tu khach nhom than thiet la bao nhieu')
        for slots, text in [({'metric': 'orders', 'period': month}, 'Tuần trước bán bao nhiêu đơn, mấy ly?'),
                            ({'metric': 'orders', 'period': month}, 'tháng 4/2026 xe giao mấy đợt hàng'),
                            ({'metric': 'growth', 'period': month}, 'Số ly bán ra kể từ ngày tăng giá'),
                            ({'metric': 'debt_collected', 'period': month}, 'tinh lai gop thang 12/2025'),
                            ({'metric': 'share', 'period': month, 'filters': {'payment': 'cod'}}, 'dthu cac don tra bang ck chiem bao nhieu phan tram')]:
            with self.assertRaises(Unsupported, msg=text):
                check_evidence(slots, text)

    def test_plain_date_phrases_override_the_model(self):
        wrong = {'kind': 'range', 'start': '2026-05-01', 'end': '2026-05-16'}
        cases = [('growth', 'dthu thang 2/2026 so vs cung ky nam ngoai thay doi bao nhieu %', {'kind': 'month', 'month': 2, 'year': 2026}, 'last_year'),
                 ('growth', 'Doanh thu từ đầu tháng này đến hết hôm nay hơn hay kém cùng kỳ tháng trước mấy %?', {'kind': 'this_month'}, None),
                 ('growth', 'Doanh thu tháng 8/2026 so với tháng 8/2025 tăng hay giảm bao nhiêu %?', {'kind': 'month', 'month': 8, 'year': 2026}, 'last_year'),
                 ('orders', 'so don quy 2/2025 la bn', {'kind': 'quarter', 'quarter': 2, 'year': 2025}, None),
                 ('average_order', 'trung bình mỗi đơn tuần rồi mang về bn', {'kind': 'last_week'}, None),
                 ('orders', 'tu dau nam den het bua nay co bn don hang', {'kind': 'year_to_date'}, None),
                 ('revenue', 'Tính từ ngày 1/1 đến hết hôm nay, doanh thu năm nay là bao nhiêu?', {'kind': 'year_to_date'}, None),
                 ('share', 'nhom A chiem bao nhieu phan tram doanh thu thang 1/2025', {'kind': 'month', 'month': 1, 'year': 2025}, None)]
        for metric, text, period, against in cases:
            slots = {'metric': metric, 'period': dict(wrong), 'against': 'last_month'} if metric == 'growth' else {'metric': metric, 'period': dict(wrong)}
            if 'nhom A' in text:
                slots['filters'] = {'product_group': 'A'}
            check_evidence(slots, text)
            self.assertEqual((slots['period'], slots.get('against')), (period, against), text)

    def test_named_shift_is_read_from_the_question(self):
        slots = {'metric': 'revenue', 'period': {'kind': 'last_week', 'shift': 'morning'}}
        check_evidence(slots, 'dthu ca chieu tuan truoc la bao nhieu')
        self.assertEqual(slots['filters'], {'shift': 'afternoon'})

    def test_runner_abstains_outside_the_layer_and_on_bad_output(self):
        question = {'id': 'q', 'question': 'doanh thu', 'as_of_date': '2025-03-31'}
        for raw, status in [('{"metric":"other"}', 'out_of_layer'), ('not json', 'slot_error'), ('{"metric":"revenue"}', 'slot_error')]:
            record = answer(question, self.engine, SimpleNamespace(chat=lambda *a, **k: (raw, {}, 'stop')))
            self.assertEqual((record['status'], record['final_answer']['type']), (status, 'abstain'))
        good = '{"metric":"revenue","period":{"kind":"this_month"}}'
        record = answer(question, self.engine, SimpleNamespace(chat=lambda *a, **k: (good, {}, 'stop')))
        self.assertEqual(record['final_answer'], {'id': 'q', 'type': 'number', 'answer': 100})

    def test_rewrite_lets_shorthand_through(self):
        import scripts.run_metric_layer as runner
        from datamate.rewrite import REWRITE_REQUEST
        question = {'id': 'q', 'question': 'dt thang 3/2025 bn', 'as_of_date': '2025-03-31'}

        def chat(messages, *a, **k):
            if messages[0]['content'] == REWRITE_REQUEST:
                return '{"question": "Doanh thu tháng 3/2025 là bao nhiêu?"}', {}, 'stop'
            return '{"metric":"revenue","period":{"kind":"month","month":3,"year":2025}}', {}, 'stop'
        self.addCleanup(setattr, runner, 'rewrite_on', True)
        runner.rewrite_on = False
        self.assertEqual(answer(question, self.engine, SimpleNamespace(chat=chat))['final_answer']['type'], 'abstain')
        runner.rewrite_on = True
        record = answer(question, self.engine, SimpleNamespace(chat=chat))
        self.assertEqual((record['rewrite'], record['final_answer']['answer']), ('Doanh thu tháng 3/2025 là bao nhiêu?', 100))


class StandardMonthsTest(unittest.TestCase):
    def test_forms(self):
        from datamate.metrics import standard_months
        for text in ['t11/2025', 'th11/2025', 'th 11-2025', 'thang 11/25', 'thang 11 nam 2025', '11/2025', '11-2025', '11/25', '11-25']:
            self.assertEqual(standard_months(text + ' dthu'), 'thang 11/2025 dthu')
        for text in ['ngay 22/06/2026', '2025-06-08', 't13/2025', 'chiem 25/100', 'quy 2/2025', 'q2/2025', 'ngay 25/12', '11/12', '11/25/2026', 'chiem 13/25']:
            self.assertEqual(standard_months(text), text)


class ChannelEvidenceTest(unittest.TestCase):
    def test_invented_channel_is_rejected(self):
        with self.assertRaises(Unsupported):
            check_evidence({'metric': 'revenue', 'period': {'kind': 'quarter', 'quarter': 2, 'year': 2025}, 'filters': {'channel': 'store'}}, 'quy 2/2025 doanh thu tiem minh duoc bao nhieu')


if __name__ == '__main__':
    unittest.main()
