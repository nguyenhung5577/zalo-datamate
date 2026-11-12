"""Rules that need raw columns the common tables drop: cost on sale lines, staff on orders, suppliers on purchases, and the lunar calendar.
Each raw row is reached through the lineage saved on the normalized row; a shop without the column declines."""
from datetime import date, timedelta
import json
import re
import sqlite3

from datamate.direct import HOW_MANY, ITEM, any_period
from datamate.metrics import money, two
from datamate.normalized import name_key, parse_date, parse_number

PERCENT = r'%|phan tram|ti le|ty le'


def compact(column):
    return re.sub(r'[^a-z0-9]', '', name_key(column or '', accents=True))


def number(value):
    try:
        return parse_number(value)
    except (TypeError, ValueError):
        return None


class Ledgers:
    def __init__(self, direct):
        self.direct, self.metrics = direct, direct.metrics
        self.raw = sqlite3.connect(f'file:{self.metrics.data.raw.path}?mode=ro', uri=True)
        self.common = sqlite3.connect(f'file:{self.metrics.data.path}?mode=ro', uri=True)
        names = [row[0] for row in self.raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        self.columns = {name: [row[1] for row in self.raw.execute(f'PRAGMA table_info("{name}")')] for name in names}
        self.loaded = {}

    def rows_of(self, table):
        """Raw rows of one table keyed by their row number, read only when first needed."""
        if table not in self.loaded:
            cursor = self.raw.execute(f'SELECT * FROM "{table}"')
            titles = [item[0] for item in cursor.description]
            self.loaded[table] = {str(row[titles.index('__dm_row')]): dict(zip(titles, row)) for row in cursor}
        return self.loaded[table]

    def source(self, lineage):
        where = json.loads(lineage)
        return self.rows_of(where['table']).get(str(where['row']), {})

    def lines(self, start, end):
        return self.common.execute("""SELECT l.order_id, l.product_id, p.product_name, l.quantity, l.line_revenue, l.lineage, o.lineage FROM order_lines l JOIN orders o USING(order_id)
            JOIN products p USING(product_id) WHERE o.status IN ('completed', 'shipping') AND o.order_date BETWEEN ? AND ?""", [start.isoformat(), end.isoformat()]).fetchall()

    def profit(self, span):
        """Revenue less quantity times the cost written on each sale line, by product; None when a line has no cost."""
        totals = {}
        for _, product, name, quantity, revenue, lineage, _ in self.lines(*span):
            row = self.source(lineage)
            found = [v for k, v in row.items() if 'giavon' in compact(k) and 'hientai' not in compact(k)]
            cost = number(found[0]) if len(found) == 1 else None
            if cost is None or quantity is None:
                return None
            totals[name] = totals.get(name, 0) + (revenue or 0) - quantity * cost
        return totals

    def expenses(self, span):
        """The month's total from the shop's expense book; None when there is no single clear book."""
        books = [t for t in self.columns if re.search(r'chi_?phi', compact(t))]
        if len(books) != 1:
            return None
        titles = self.columns[books[0]]
        days, amounts = [c for c in titles if compact(c).startswith('ngay')], [c for c in titles if re.search(r'^(?:sotien|tienchi|chiphi|tien)', compact(c))]
        if len(days) != 1 or len(amounts) != 1:
            return None
        total = 0
        for row in self.rows_of(books[0]).values():
            day = parse_date(row[days[0]]) if row[days[0]] else None
            if day and span[0].isoformat() <= day <= span[1].isoformat():
                total += number(row[amounts[0]]) or 0
        return total

    def gross_profit(self, plain, as_of):
        asked_item = re.search(r'\b(?:hang|mat hang|san pham|sp|mon) nao\b|\b(?:san pham|mat hang|sp|mon)\b.*\bla gi\b', plain)
        if not re.search(r'lai gop|lai rong|\bloi nhat|\bloi nhieu|cho loi|nhieu loi|\blai nhieu', plain) or re.search(r'nhom|nhan vien', plain):
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        totals = self.profit(span)
        if not totals:
            return None
        if asked_item and re.search(r'nhat|nhut', plain) and 'lai rong' not in plain:
            best = sorted(totals.items(), key=lambda item: -item[1])
            return None if len(best) > 1 and best[0][1] == best[1][1] else {'type': 'string', 'answer': best[0][0]}
        if asked_item or self.direct.product(plain) is not None or not re.search(HOW_MANY + r'|\bla\b|duoc|\bbiet\b|\bcho\b|\btinh\b', plain):
            return None
        total = sum(totals.values())
        if 'lai rong' in plain:
            spent = self.expenses(span)
            if spent is None:
                return None
            total -= spent
        elif 'lai gop' not in plain:
            return None
        return {'type': 'number', 'answer': money(total)}

    def staff_of(self, line, order):
        for lineage in (order, line):
            row = self.source(lineage)
            found = [v for k, v in row.items() if re.search(r'nhanvien|nguoiban|manv', compact(k)) and 'tao' not in compact(k)]
            if len(found) == 1 and found[0]:
                return str(found[0])
        return None

    def staff_names(self):
        """Staff code to name, for shops that keep a staff list."""
        for table, titles in self.columns.items():
            codes = [c for c in titles if compact(c) in ('manhanvien', 'manv')]
            names = [c for c in titles if compact(c) in ('tengoi', 'hoten', 'tennhanvien', 'ten')]
            if 'nhan' in compact(table) and codes and names:
                return {str(r[codes[0]]): r[names[0]] for r in self.rows_of(table).values()}
        return {}

    def staff(self, plain, as_of):
        if not re.search(r'\b(?:nhan vien|nv|nv\d+)\b', plain) or re.search(r'don hang|\bso don|may don|%|phan tram|chiem', plain) or not re.search(r'tien|doanh thu|dthu|gioi', plain):
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        return self.staff_sales(plain, span, re.search(r'nhan vien nao|nv nao|nhan vien ban gioi|\bai\b', plain) and re.search(r'nhat|nhut', plain))

    def staff_sales(self, plain, span, best):
        """Revenue of the staff member the question names, or the name of the best seller (rulebook: by revenue)."""
        totals = {}
        for _, _, _, _, revenue, line, order in self.lines(*span):
            person = self.staff_of(line, order)
            if person is None:
                return None
            totals[person] = totals.get(person, 0) + (revenue or 0)
        names = self.staff_names()
        if best:
            best = sorted(totals.items(), key=lambda item: -item[1])
            if not best or (len(best) > 1 and best[0][1] == best[1][1]):
                return None
            return {'type': 'string', 'answer': names.get(best[0][0], best[0][0])}
        code = re.search(r'\bnv ?(\d+)\b', plain)
        wanted = [k for k in totals if code and compact(k) == 'nv' + code[1]]
        if not code:
            wanted = [k for k in totals if compact(names.get(k, k)) and name_key(names.get(k, k), accents=True) in plain]
        return {'type': 'number', 'answer': money(totals[wanted[0]])} if len(wanted) == 1 else None

    def suppliers(self):
        """Purchase slip to supplier, and supplier code to name, whichever table holds them."""
        slips, names = {}, {}
        known = {str(row[0]) for row in self.common.execute('SELECT DISTINCT import_id FROM purchase_lines')}
        for table, titles in self.columns.items():
            found = [c for c in titles if compact(c) in ('nhacungcap', 'mancc', 'manhacungcap')]
            if not found:
                continue
            rows = self.rows_of(table).values()
            for column in titles:
                if column != found[0] and known and sum(str(r[column]) in known for r in rows) >= len(rows) * 0.9 and rows:
                    slips.update({str(r[column]): str(r[found[0]]) for r in rows})
            label = [c for c in titles if compact(c) in ('tengoi', 'tennhacungcap', 'tenncc')]
            if label and 'cungcap' in compact(table):
                names = {str(r[found[0]]): r[label[0]] for r in rows}
        return slips, names

    def supplier(self, plain, as_of):
        main = re.search(r'(?:nha cung cap|ncc) chinh', plain)
        if not re.search(r'nha cung cap|\bncc\b', plain) or not (main or re.search(r'(?:nhieu|cao|lon) ?(?:tien )?nh[au]t|nhieu tien', plain)) or not re.search(r'\bnao\b|\bai\b|la ai', plain):
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        return self.top_supplier(span, main)

    def top_supplier(self, span, main):
        """The supplier with the most import money in the span (rulebook), or with the most slips for the main one."""
        slips, names = self.suppliers()
        money_by, count_by = {}, {}
        for slip, status, total in self.common.execute("""SELECT import_id, source_status, COALESCE(recorded_total, quantity * unit_price, 0) FROM purchase_lines
                WHERE import_date BETWEEN ? AND ?""", [span[0].isoformat(), span[1].isoformat()]):
            if 'huy' in name_key(status or '', accents=True):
                continue
            who = slips.get(str(slip))
            if who is None:
                return None
            money_by[who] = money_by.get(who, 0) + total
            count_by.setdefault(who, set()).add(slip)
        ranked = sorted(((len(v), k) for k, v in count_by.items()) if main else ((v, k) for k, v in money_by.items()), reverse=True)
        if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
            return None
        return {'type': 'string', 'answer': names.get(ranked[0][1], ranked[0][1])}

    def tet_range(self, year):
        def solar(month, day, lunar):
            found = self.raw.execute('SELECT date FROM calendar WHERE lunar_month = ? AND lunar_day = ? AND lunar_year = ? AND lunar_leap = ?', [month, day, lunar, 'false']).fetchall()
            return date.fromisoformat(found[0][0]) if len(found) == 1 else None
        first, last = solar(12, 23, year - 1), solar(1, 10, year)
        return (first, last) if first and last else None

    def tet(self, plain, as_of):
        if not re.search(r'\btet\b', plain) or re.search(r'duong lich|nhom|tai quay|tien mat|khach|nhan vien', plain):
            return None
        named = re.search(r'\btet (?:am lich |nguyen dan |co truyen )?(?:nam )?(20\d\d)\b', plain)
        year = int(named[1]) if named else date.fromisoformat(as_of).year if re.search(r'tet (?:nam nay|nay)', plain) else None
        span = self.tet_range(year) if year else None
        if span is None:
            return None
        revenue = lambda s: self.metrics.revenue(s[0], s[1], None)
        if re.search(PERCENT, plain) or re.search(r'hon hay kem|cao hay thap', plain):
            if re.search(r'truoc tet', plain):
                size = (span[1] - span[0]).days + 1
                said = re.search(r'(\d+) ngay truoc tet', plain)
                if said and int(said[1]) != size:
                    return None
                other = (span[0] - timedelta(size), span[0] - timedelta(1))
            elif re.search(r'tet (?:nam )?(?:ngoai|truoc|trc|\d{4}).*tet|tet nam (?:ngoai|truoc|trc)', plain):
                other = self.tet_range(year - 1)
            else:
                return None
            base = revenue(other) if other else 0
            return {'type': 'percent', 'answer': two(100 * (revenue(span) - base) / base)} if base else None
        if re.search(r'ban chay|ban nhieu nhat|hot nhat', plain) and re.search(ITEM + r' (?:nao|gi)|nao', plain):
            rows = self.metrics.rows('SELECT MIN(product_name), SUM(basic_quantity) FROM sales WHERE NOT COALESCE(is_service, 0) AND order_date BETWEEN ? AND ? GROUP BY product_id ORDER BY 2 DESC',
                                     [span[0].isoformat(), span[1].isoformat()])
            return {'type': 'string', 'answer': rows[0][0]} if rows and (len(rows) == 1 or rows[0][1] != rows[1][1]) else None
        if re.search(r'doanh thu|dthu', plain) and re.search(HOW_MANY + r'|\bla\b', plain):
            return {'type': 'number', 'answer': money(revenue(span))}
        return None

    def raise_day(self, product, plain):
        """The first day the price table moved up to the price the question names (rulebook section 5)."""
        said = re.search(r'\blen (?:gia )?(\d[\d.,]*) ?(k|nghin|ngan)?', plain)
        if not said:
            return None
        price = float(re.sub(r'[.,]', '', said[1])) * (1000 if said[2] else 1)
        for table, titles in self.columns.items():
            days = [c for c in titles if compact(c).startswith('ngay')]
            prices = [c for c in titles if compact(c) in ('giamoi', 'giabanle')]
            if 'gia' not in compact(table) or len(days) != 1 or len(prices) != 1:
                continue
            old = [c for c in titles if compact(c) == 'giacu']
            names = {product[0], name_key(product[1], accents=True)}
            mine = [r for r in self.rows_of(table).values() if any(str(v) in names or name_key(str(v), accents=True) in names for k, v in r.items() if not k.startswith('__'))]
            mine.sort(key=lambda r: parse_date(r[days[0]]))
            before = None
            for row in mine:
                now = number(row[prices[0]])
                last = number(row[old[0]]) if old else before
                if now == price and last is not None and now > last:
                    return parse_date(row[days[0]])
                before = now
        return None

    def factor(self, product_id, unit):
        """Base units in one `unit` of a product; None when the shop does not say."""
        for sale_unit, factor in self.common.execute('SELECT sale_unit, factor FROM unit_conversions WHERE product_id = ?', [product_id]):
            if name_key(sale_unit or '', accents=True) == unit and factor:
                return factor
        return None

    def kardex(self, day):
        """Each item's balance at the end of `day` from a stock card; None when the shop keeps none."""
        for table, titles in self.columns.items():
            balance = [c for c in titles if compact(c) == 'toncuoi']
            when = [c for c in titles if re.search(r'ngaygio|thoigian', compact(c))]
            item = [c for c in titles if compact(c) in ('mahanghoa', 'mahang', 'masp')]
            if len(balance) == 1 and len(when) == 1 and len(item) == 1:
                latest = {}
                for row in sorted(self.rows_of(table).values(), key=lambda r: str(r[when[0]])):
                    if str(row[when[0]])[:10] <= day.isoformat():
                        latest[str(row[item[0]])] = number(row[balance[0]]) or 0
                return latest
        return None

    def on_hand(self, day, as_of):
        """Stock by item at the end of a day: the stock card for any day, the stock report only for today."""
        found = self.kardex(day)
        if found is not None:
            return found
        if day.isoformat() != as_of:
            return None
        report = {str(row[0]): row[1] or 0 for row in self.common.execute('SELECT product_id, on_hand FROM stock_report')}
        if report:
            return report
        # A shop that keeps lots instead of a stock report: what is left of each lot, added up by item.
        left = {}
        for item, _, amount, _ in self.lots(as_of):
            left[str(item)] = left.get(str(item), 0) + amount
        return left or None

    def stock_day(self, plain, as_of):
        days = re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain)
        if len(days) > 1:
            return None
        return date(int(days[0][2]), int(days[0][1]), int(days[0][0])) if days else date.fromisoformat(as_of)

    def stock_left(self, plain, as_of, product=None):
        """How much of one item is left, in its base unit or as whole big units plus loose base units; the model's form may name the item."""
        if not re.search(r'\bton\b|trong kho|\bcon\b', plain):
            return None
        product, day = product or self.direct.product(plain), self.stock_day(plain, as_of)
        if product is None or day is None:
            return None
        bare = plain.replace(' '.join(product[3]), ' ')
        if re.search(r'\bban\b|\bnhap\b|han dung|het han|(?<!may )(?<!nhieu )(?<!bnhiu )(?<!bn )\blo\b|gia von|\btien\b', bare):
            return None
        left = self.on_hand(day, as_of)
        if left is None or product[0] not in left:
            return None
        amount = left[product[0]]
        pair = re.search(r'(?:may|bao nhieu|bnhiu|bn) ([\w ]+?) (?:chan|nguyen)\b', bare)
        if pair and re.search(r'\ble\b', bare):
            size = self.factor(product[0], pair[1].strip())
            loose = re.search(r'(?:may|bao nhieu|bnhiu|bn) (\w+) le\b', bare)
            base_key = name_key(product[2] or '', accents=True)
            small = 1 if not loose or loose[1] == base_key else self.factor(product[0], loose[1])
            if size and small and (amount % size) % small == 0:
                return {'type': 'list', 'answer': [int(amount // size), int((amount % size) // small)]}
            return None
        unit = product[2] or self.direct.only_sale_unit(product[0])
        if unit and re.search(r'(?:may|bao nhieu|bnhiu|\bbn|nhieu) ' + re.escape(name_key(unit, accents=True)) + r'\b', plain):
            return {'type': 'number', 'answer': amount}
        return None

    def catalogue_costs(self):
        """Cost of one base unit written in the catalogue, by product id."""
        ids = {}
        for product_id, name in self.common.execute('SELECT product_id, product_name FROM products'):
            ids[str(product_id)] = str(product_id)
            ids[name_key(name or '', accents=True)] = str(product_id)
        for table, titles in self.columns.items():
            found = [c for c in titles if 'giavon' in compact(c) and 'lucban' not in compact(c)]
            if not found:
                continue
            costs, repeated = {}, False
            for row in self.rows_of(table).values():
                who = next((ids[k] for k in (str(v) if str(v) in ids else name_key(str(v), accents=True) for c, v in row.items() if v is not None and not c.startswith('__')) if k in ids), None)
                if who is not None:
                    repeated = repeated or who in costs
                    costs[who] = number(row[found[-1]])
            # Sale lines also carry a cost, but only a catalogue lists each item once.
            if costs and not repeated:
                return costs
        return None

    def stock_value(self, plain, as_of):
        if not re.search(r'trong kho|ton kho|hang ton', plain) or not re.search(r'gia von|dang (?:bao nhieu|gia)', plain) or self.direct.product(plain) is not None:
            return None
        day = self.stock_day(plain, as_of)
        left = self.on_hand(day, as_of) if day else None
        costs = self.catalogue_costs()
        if not left or not costs:
            return None
        return {'type': 'number', 'answer': money(sum(amount * (costs.get(item) or 0) for item, amount in left.items() if amount > 0))}

    def lots(self, as_of):
        """Lots still holding stock today, with item, expiry, quantity left and purchase price of one base unit."""
        held = {}
        for lot, sold, returned, status in self.common.execute('SELECT s.lot_id, s.sold_quantity, s.returned_quantity, o.status FROM lot_sales s JOIN orders o USING(order_id)'):
            if status != 'cancelled':
                held[lot] = held.get(lot, 0) + (sold or 0) - (returned or 0)
        found = []
        for lot, item, imported, lineage in self.common.execute('SELECT lot_id, product_id, imported_quantity, lineage FROM inventory_lots WHERE import_date <= ?', [as_of]):
            row = self.source(lineage)
            ends = [v for k, v in row.items() if compact(k) in ('hansudung', 'hsd', 'handung')]
            prices = [v for k, v in row.items() if re.search(r'gianhap', compact(k))]
            left = (imported or 0) - held.get(lot, 0)
            if len(ends) == 1 and left > 0:
                found.append((item, parse_date(ends[0]), left, number(prices[0]) if len(prices) == 1 else None))
        return found

    def expiry(self, plain, as_of):
        if not re.search(r'het han|han dung|\bhsd\b|can date', plain) or not re.search(r'\blo\b|\bton\b|con hang|trong kho|con lai|\bcon\b', plain + ' '):
            return None
        if 'inventory_lots' not in self.metrics.capabilities:
            return {'type': 'unanswerable', 'reason': 'shop does not record lots'}
        today = date.fromisoformat(as_of)
        lots = [lot for lot in self.lots(as_of) if lot[1] is not None and lot[1] >= as_of]
        product = self.direct.product(plain)
        if product is not None:
            if not re.search(r'gan het han|can date|som nhat', plain):
                return None
            mine = [lot[1] for lot in lots if lot[0] == product[0]]
            return {'type': 'date', 'answer': min(mine)} if mine else None
        ahead = re.search(r'(\d+) ngay (?:toi|nua|sap toi)', plain)
        days = re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain)
        written = [date(int(y), int(m), int(d)) for d, m, y in days]
        if ahead:
            first, last = today, today + timedelta(int(ahead[1]))
        elif len(written) == 2:
            first, last = written
        else:
            return None
        inside = [lot for lot in lots if first.isoformat() <= lot[1] <= last.isoformat()]
        if re.search(r'tien von|tien|von', plain):
            return {'type': 'number', 'answer': money(sum(lot[2] * lot[3] for lot in inside))} if all(lot[3] is not None for lot in inside) else None
        items = {lot[0] for lot in inside}
        if re.search(HOW_MANY + r' lo\b', plain):
            return {'type': 'number', 'answer': len(inside)}
        if re.search(HOW_MANY + ' ' + ITEM + r'\b', plain):
            return {'type': 'number', 'answer': len(items)}
        names = dict(self.common.execute('SELECT product_id, product_name FROM products'))
        soonest = {}
        for lot in sorted(inside, key=lambda lot: lot[1]):
            soonest.setdefault(lot[0], lot[1])
        return {'type': 'list', 'answer': [names[item] for item in soonest]}

    def import_minus_sold_for(self, product, span, minus=True):
        total = 0
        for unit, quantity, status in self.common.execute('SELECT purchase_unit, quantity, source_status FROM purchase_lines WHERE product_id = ? AND import_date BETWEEN ? AND ?',
                                                          [product[0], span[0].isoformat(), span[1].isoformat()]):
            if 'huy' in name_key(status or '', accents=True):
                continue
            key = name_key(unit or '', accents=True)
            size = 1 if not key or key == name_key(product[2] or '', accents=True) else self.factor(product[0], key)
            if size is None:
                return None
            total += (quantity or 0) * size
        if not minus:
            return {'type': 'number', 'answer': int(total) if float(total).is_integer() else total} if total else None
        sold = self.direct.quantity(product[0], span[0].isoformat(), span[1].isoformat())
        diff = total - sold
        return {'type': 'number', 'answer': int(diff) if float(diff).is_integer() else diff}

    def import_minus_sold(self, plain, as_of):
        """Units brought in for one item, less what was sold when the question says so; a line with no unit is in the base unit."""
        minus = re.search(r'\btru\b|chenh', plain)
        if not re.search(r'nhap', plain) or re.search(r'\bgia\b|\btien\b|\bnao\b', self.direct.without_product(plain)) or not (minus or re.search(r'nhap (?:ve|vao)? ?' + HOW_MANY, plain)):
            return None
        product, span = self.direct.product(plain), any_period(plain, as_of)
        if product is None or span is None:
            return None
        return self.import_minus_sold_for(product, span, bool(minus))

    def out_of_stock(self, plain, as_of):
        if not re.search(r'het hang|chay hang|dut hang', plain) or not re.search(HOW_MANY + ' ' + ITEM + r'\b|' + ITEM + ' dang', plain):
            return None
        day = self.stock_day(plain, as_of)
        left = self.on_hand(day, as_of) if day else None
        if left is None:
            return None
        bought = {row[0] for row in self.common.execute('SELECT DISTINCT product_id FROM purchase_lines')}
        return {'type': 'number', 'answer': sum(amount == 0 for item, amount in left.items() if item in bought)}

    def has_column(self, pattern):
        return any(re.search(pattern, compact(c)) for titles in self.columns.values() for c in titles)

    def answer(self, plain, as_of):
        for rule in (self.gross_profit, self.staff, self.supplier, self.tet, self.expiry, self.import_minus_sold, self.stock_value, self.out_of_stock, self.stock_left):
            try:
                found = rule(plain, as_of)
            except (ValueError, TypeError, KeyError, ZeroDivisionError, sqlite3.Error):
                continue
            if found is not None:
                return found
        return None
