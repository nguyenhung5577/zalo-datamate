"""Common shop questions that used to wait for the model: filters are read by code and the fixed metrics compute the answer."""
import re
import sqlite3

from datetime import date, timedelta

from datamate.metrics import Unsupported, money, two
from datamate.normalized import name_key, parse_date
from datamate.direct import HOW_MANY, any_period, debt_day

PERCENT = r'%|phan tram|ti le|ty le|ti trong|ty trong|chiem'
SHIFT = {'ca sang': 'morning', 'ca chieu': 'afternoon'}
PAYMENT = {'tien mat': 'cash', 'tm': 'cash', 'chuyen khoan': 'transfer', 'ck': 'transfer', 'vi dien tu': 'ewallet', 'cod': 'cod', 'ghi no': 'debt'}
CHANNEL = {'tai quay': r'store|tai ', 'dien thoai': r'phone|dien thoai', 'zalo': r'zalo',
           'san thuong mai dien tu': r'\bsan\b|tmdt|marketplace', 'san tmdt': r'\bsan\b|tmdt|marketplace', 'tren san': r'\bsan\b|tmdt|marketplace'}
# Words that change what is asked and that no rule here reads.
OTHER = r'\bnhom\b|\bnhan vien\b|\bcuoi tuan\b|\bchu nhat\b|\bthu \d|\bkhach (?:nu|nam)\b|\bduong\b|\bsize\b|\bnao\b|\bso voi\b|\bso vs\b|\bhon\b|\bkem\b|\bchenh\b|\btang\b|\bgiam\b|\bnhap\b|(?<!mang )(?<!con )(?<!tra )\blai\b|\bloi\b|\btet\b|\bcung ky\b'


class Common:
    def __init__(self, direct):
        self.direct, self.metrics = direct, direct.metrics

    def answer(self, plain, as_of):
        for rule in (self.cancelled, self.average, self.shift_gap, self.debt_total, self.debt_collected, self.filtered, self.top_customers, self.sized, self.group_money, self.tier_money, self.price_on_day, self.rounds, self.split_orders, self.delivered, self.due_orders):
            try:
                found = rule(plain, as_of)
            except (Unsupported, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, sqlite3.Error):
                continue
            if found is not None:
                return found
        return None

    def inside(self, span):
        """A period wholly outside the shop's dates cannot be answered (rulebook section 7)."""
        return not (span[1].isoformat() < self.metrics.first or span[0].isoformat() > self.metrics.last)

    def period(self, plain, as_of):
        return any_period(plain, as_of) if self.direct.named_product(plain) is None else None

    def missing(self):
        return {'type': 'unanswerable', 'reason': 'period is outside the data'}

    def cancelled(self, plain, as_of):
        if not re.search(r'\bhuy\b', plain) or not re.search(r'\bdon\b|\bdh\b|\bbill\b', plain) or re.search(OTHER + r'|\bvi\b|\bca (?:sang|chieu)\b|\btien\b', plain):
            return None
        span = self.period(plain, as_of)
        if span is None:
            return None
        if not self.inside(span):
            return self.missing()
        if re.search(PERCENT, plain):
            return self.metrics.metric_cancel_rate({}, *span, as_of)
        return self.metrics.metric_cancelled({}, *span, as_of) if re.search(HOW_MANY + r'|\bdem\b|\bso don\b', plain) else None

    def named_filter(self, plain):
        """The one shift, payment way or channel a question names; a second one declines."""
        filters = {'shift': value for word, value in SHIFT.items() if re.search(r'\b' + word + r'\b', plain)}
        labels = self.metrics.labels()
        for word, value in PAYMENT.items():
            if re.search(r'\b' + word + r'\b', plain):
                if 'payment_method' not in self.metrics.capabilities:
                    raise Unsupported('no payment way')
                filters['payment'] = value
        for word, hint in CHANNEL.items():
            if re.search(r'\b(?:qua|ban|tai|o) ' + word + r'\b|\b' + word + r'\b', plain) and not self.direct.product(plain):
                found = [label for label in labels['channel'] if re.search(hint, name_key(label, accents=True))]
                if len(found) == 1:
                    filters['channel'] = found[0]
        return filters

    def average(self, plain, as_of):
        per_order = r'(?:(?:mot|moi|1|tren) (?:mot |1 )?(?:don|dh|bill)\b|(?:trung binh|binh quan|\bbq\b) (?:don|dh|bill)\b)'
        if not re.search(r'trung binh|binh quan|\bbq\b', plain) or not re.search(per_order, plain) or re.search(OTHER.replace(r'\bnhom\b|', '') + r'|\bhuy\b|\bngay\b.*\bngay\b', plain):
            return None
        if not re.search(r'doanh thu|dthu|gia tri|\btien\b', plain):
            return None
        span = self.period(plain, as_of)
        if span is None:
            return None
        filters = self.named_filter(plain)
        filters.update({'customer_group': label for label in self.metrics.labels()['customer_group'] if re.search(r'\bnhom (?:khach )?' + re.escape(name_key(label, accents=True)) + r'\b', plain)})
        return self.metrics.metric_average_order({'filters': filters}, *span, as_of) if self.inside(span) else self.missing()

    def shift_gap(self, plain, as_of):
        if not re.search(r'\bca chieu\b', plain) or not re.search(r'\bca sang\b', plain) or not re.search(r'%|phan tram', plain):
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        if not self.inside(span):
            return self.missing()
        orders, money_asked = re.search(r'\bdon\b|\bdh\b', plain), re.search(r'doanh thu|dthu|\btien\b', plain)
        if bool(orders) == bool(money_asked):
            return None
        return self.metrics.metric_shift_gap({'of': 'orders' if orders else 'revenue'}, *span, as_of)

    def filtered(self, plain, as_of):
        """Revenue, orders or share of revenue under one shift, payment way or channel."""
        if re.search(OTHER + r'|\bhuy\b|\btra lai\b|\bno\b|\bmua chiu\b|trung binh|binh quan|bin quan|moi don|lan luot|moi loai|\bva\b|\bvoi\b', plain):
            return None
        filters = self.named_filter(plain)
        if len(filters) != 1:
            return None
        span = self.period(plain, as_of)
        if span is None:
            return None
        if not self.inside(span):
            return self.missing()
        if re.search(PERCENT, plain):
            return self.metrics.metric_share({'filters': filters}, *span, as_of) if re.search(r'doanh thu|dthu|\btien\b', plain) else None
        orders, money_asked = re.search(r'\bdon\b|\bdh\b|\bbill\b', plain), re.search(r'doanh thu|dthu|\btien\b', plain)
        if orders and money_asked and re.search(r'doanh thu|dthu|tien (?:ban|hang)|bao nhieu tien|bnhiu tien|may tien', plain):
            orders = None
        if bool(orders) == bool(money_asked):
            return None
        if orders:
            return None if re.search(r'\bdt\b', plain) else self.metrics.metric_orders({'filters': filters}, *span, as_of)
        return self.metrics.metric_revenue({'filters': filters}, *span, as_of)

    def debt_total(self, plain, as_of):
        end = debt_day(plain, as_of)
        if end is None or not re.search(r'\bcon no\b|\bcong no\b|\bcon thieu\b|\bno chua thu\b', plain) or not re.search(r'\btong\b|\btien\b', plain) or not re.search(HOW_MANY + r'|\bnhieu\b', plain):
            return None
        if re.search(r'\bnao\b|tuoi no|\bgop\b|\bnhat\b|\bdon\b.*\bnhom\b|\bqua \d|\bhon \d|\bnhom\b|(?<![/\d-])\d{1,2} (?:khach|nguoi)\b|' + HOW_MANY + r' (?:khach|nguoi)', plain):
            return None
        return self.metrics.metric_debt_total({}, end, end, as_of)

    def debt_collected(self, plain, as_of):
        if not re.search(r'\btra (?:bot )?no\b|\bthu hoi\b|\bthu no\b|\bdoi duoc\b.*\bno\b', plain) or re.search(r'\bgop\b|\bnao\b|\bnhat\b|\bnhom\b', plain) or not re.search(HOW_MANY + r'|\bnhieu\b|\btong\b', plain):
            return None
        span = self.period(plain, as_of)
        if span is None:
            return None
        return self.metrics.metric_debt_collected({}, *span, as_of) if self.inside(span) else self.missing()

    def top_customers(self, plain, as_of):
        count = re.search(r'(?:top |(?<![/\d-]))(\d{1,2}) (?:khach|kh)\b', plain)
        if not count or not re.search(r'nhieu tien|chi tieu nhieu|chi nhieu|tong tien mua (?:hang )?cao|mua nhieu tien', plain) or not re.search(r'nhat|nhut', plain) or re.search(r'\bno\b|\bnhom\b|\bduong\b|nhan vien', plain):
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        if not self.direct.has_customers():
            return {'type': 'unanswerable', 'reason': 'shop does not record customers'}
        rows = self.metrics.sales('s.customer_name, SUM(s.line_revenue)', *span, None, 'AND s.customer_id IS NOT NULL GROUP BY s.customer_id ORDER BY 2 DESC, s.customer_id')
        size = int(count[1])
        if len(rows) < size or (len(rows) > size and rows[size - 1][1] == rows[size][1]):
            return None
        return {'type': 'list', 'answer': [row[0] for row in rows[:size]]}

    def sized(self, plain, as_of):
        """Cups of one size across all drinks; a size is written in the product name."""
        size = re.search(r'\bsize ([sml])\b', plain)
        if not size or not re.search(HOW_MANY + r' ly|\bso ly\b', plain) or re.search(r'\bnhom\b|nhap|\bnao\b|%|phan tram|nhan vien|\bkhach\b', plain) or self.direct.product(plain) is not None:
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        sold = self.metrics.rows('SELECT SUM(basic_quantity) FROM sales WHERE order_date BETWEEN ? AND ? AND product_name LIKE ?', [span[0].isoformat(), span[1].isoformat(), '%(' + size[1].upper() + ')%'])[0][0]
        return {'type': 'number', 'answer': sold} if sold else None

    def group_money(self, plain, as_of):
        group = self.direct.group_of(plain) if re.search(r'\bnhom\b', plain) else None
        if group is None or not re.search(r'doanh thu|dthu|\btien\b', plain) or re.search(r'\bnao\b|\bkhach\b|\btang\b|\bgiam\b|\bso voi\b|\bca (?:sang|chieu)\b|' + PERCENT, plain.replace(name_key(group, accents=True), ' ')):
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        return self.metrics.metric_revenue({'filters': {'product_group': group}}, *span, as_of) if self.inside(span) else self.missing()

    def tier_money(self, plain, as_of):
        """Revenue or share of revenue from one customer group."""
        if not re.search(r'\bkhach\b.*\bnhom\b|\bnhom khach\b', plain) or re.search(r'\bnao\b|\btang\b|\bgiam\b|\bso voi\b|trung binh|bi?n?h? quan|\bmoi don\b', plain) or not re.search(r'doanh thu|dthu|\btien\b', plain):
            return None
        named = [label for label in self.metrics.labels()['customer_group'] if re.search(r'\bnhom (?:khach )?' + re.escape(name_key(label, accents=True)) + r'\b', plain)]
        span = any_period(plain, as_of)
        if len(named) != 1 or span is None:
            return None
        if not self.inside(span):
            return self.missing()
        filters = {'customer_group': named[0]}
        return self.metrics.metric_share({'filters': filters}, *span, as_of) if re.search(PERCENT, plain) else {'type': 'number', 'answer': money(self.metrics.revenue(*span, filters))}

    def price_on_day(self, plain, as_of):
        """The price written on a day's sale lines, for one base unit, cancelled and returned orders included (rulebook section 5)."""
        days = re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain)
        product = self.direct.product(plain)
        bare = self.direct.without_product(plain)
        if len(days) != 1 or product is None or not re.search(r'\bgia\b', bare) or re.search(r'\bnhap\b|\bvon\b|%|phan tram|\blen gia\b|\btang gia\b|\bdoi gia\b', bare):
            return None
        day = '%04d-%02d-%02d' % (int(days[0][2]), int(days[0][1]), int(days[0][0]))
        prices = self.metrics.rows('SELECT DISTINCT l.basic_unit_price FROM order_lines l JOIN orders o USING(order_id) WHERE l.product_id = ? AND o.order_date = ?', [product[0], day])
        return {'type': 'number', 'answer': money(prices[0][0])} if len(prices) == 1 and prices[0][0] is not None else None

    def trips(self, as_of):
        """Delivery book lines up to today as (order, round, day, product, amount); shops name these columns differently."""
        books = self.direct.books
        found = []
        for rcol in ('Lượt giao', 'Lần giao', 'Đợt giao', 'Chuyến giao', 'Luot giao', 'Lan giao', 'Dot giao'):
            rows = books.table(rcol)
            if not rows:
                continue
            for row in rows:
                names = {name_key(column, accents=True): column for column in row}
                order = next(names[n] for n in names if re.search(r'\b(?:so don|ma bill|ma don|so hoa don)\b', n))
                day = next(names[n] for n in names if re.search(r'\b(?:ngay cho|thoi gian giao|ngay giao)\b', n))
                item = next(names[n] for n in names if re.search(r'\b(?:ten hang|hang hoa)\b', n))
                amount = next(names[n] for n in names if re.search(r'\b(?:sl thuc giao|so luong giao|sl giao)\b', n))
                when = parse_date(row[day], row.get('__dm_sheet') or '') if row[day] else None
                if when and when <= as_of:
                    found.append((str(row[order]), row[rcol], when, row[item], float(row[amount] or 0)))
            break
        return found

    def rounds(self, plain, as_of):
        if not re.search(r'\bdot\b', plain) or not re.search(r'\bgiao\b|\bxe\b|\bcho\b', plain) or re.search(r'\bdon dat\b|\d dot|\bnao\b', plain) or not re.search(HOW_MANY + r'|\bdem\b', plain):
            return None
        span, lines = any_period(plain, as_of), self.trips(as_of)
        if span is None or not lines:
            return None
        return {'type': 'number', 'answer': len({(o, n) for o, n, day, _, _ in lines if span[0].isoformat() <= day <= span[1].isoformat()})}

    def ordered_in(self, plain, as_of):
        """Ids of the orders placed in the month a "don dat thang X" names."""
        span = any_period(plain, as_of) if re.search(r'\bdon dat\b|\bkhach dat\b', plain) else None
        if span is None:
            return None
        return {str(row[0]) for row in self.metrics.rows('SELECT order_id FROM orders WHERE order_date BETWEEN ? AND ?', [span[0].isoformat(), span[1].isoformat()])}

    def split_orders(self, plain, as_of):
        count = re.search(r'(\d+) dot tro len', plain)
        orders = self.ordered_in(plain, as_of) if count else None
        if orders is None or not re.search(HOW_MANY + r' don|\bdem\b', plain):
            return None
        lines = self.trips(as_of)
        if not lines:
            return None
        rounds = {}
        for order, number, _, _, _ in lines:
            rounds.setdefault(order, set()).add(number)
        return {'type': 'number', 'answer': sum(len(rounds[o]) >= int(count[1]) for o in rounds if o in orders)}

    def delivered(self, plain, as_of):
        product = self.direct.product(plain)
        orders = self.ordered_in(self.direct.without_product(plain), as_of) if product is not None else None
        if orders is None or not re.search(r'\bgiao\b|\bcho di\b|\bcho\b', plain):
            return None
        lines = self.trips(as_of)
        if not lines:
            return None
        total = sum(amount for order, _, _, item, amount in lines if order in orders and item == product[1])
        return {'type': 'number', 'answer': int(total) if total == int(total) else total}

    def due_orders(self, plain, as_of):
        """Credit orders by the due day in the debt book; a cancelled order that would change the count declines, since no rule settles it."""
        if not re.search(r'\bban chiu\b|\bmua chiu\b', plain) or not re.search(r'\bngay phai tra\b|\bhan\b', plain) or not re.search(HOW_MANY + r' don|\bdem\b', plain):
            return None
        books = self.direct.books
        due_cols = {'Ngày phải trả', 'Han tra', 'Hạn trả', 'Hạn thanh toán', 'Han thanh toan'}
        rows = [row for name, columns in books.columns.items() if columns & due_cols for row in books.table(next(iter(columns & due_cols)))]
        if not rows:
            return None
        def due(row):
            value = str(next((row.get(c) for c in due_cols if row.get(c) is not None), ''))
            return (date(1899, 12, 30) + timedelta(int(value))).isoformat() if value.isdigit() else parse_date(value, '')
        def order(row):
            return str(row.get('Mã đơn hàng') or row.get('So hoa don'))
        if re.search(r'muon hon|sau hom nay|con sau', plain):
            test = lambda day: day > as_of
        else:
            span = any_period(plain, as_of) if re.search(r'\bthang\b', plain) else None
            months = re.findall(r'\bthang (\d{1,2})/(\d{4})', plain)
            if len(months) != 1 or span is None:
                return None
            prefix = '%s-%02d' % (months[0][1], int(months[0][0]))
            test = lambda day: day[:7] == prefix
        hits = [row for row in rows if due(row) and test(due(row))]
        kept = [row for row in hits if books.status.get(order(row)) != 'cancelled']
        return {'type': 'number', 'answer': len(hits)} if len(hits) == len(kept) else None
