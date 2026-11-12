"""Typed business tables derived from source schemas, never from questions or answers."""
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata

from .data import BusinessData, iso_date, quote
from . import roles as roles_module
from .roles import canonicalize, glossary


def name_key(value, accents=False):
    text = ' '.join(str(value or '').split()).casefold()
    if accents:
        text = ''.join(c for c in unicodedata.normalize('NFD', text)
                       if unicodedata.category(c) != 'Mn').replace('đ', 'd')
    return text


def parse_number(value, quantity=False):
    if value is None or str(value).strip() == '':
        return None
    text = re.sub(r'\s+', '', str(value)).lower()
    text = re.sub(r'(vnd|vnđ|đ|₫)$', '', text)
    multiplier = 1
    short = re.fullmatch(r'(-?)([\d.,]+)(trieu|triệu|tr|nghin|nghìn|ngan|ngàn|k)(\d*)', text)
    if short:
        sign, text, unit, remainder = short.groups()
        multiplier = (1000000 if unit in ('tr', 'trieu', 'triệu') else 1000) * (-1 if sign else 1)
        text = text.replace(',', '.')
        if remainder:
            return float((Decimal(text) + Decimal('0.' + remainder)) * multiplier)
    else:
        if quantity:
            match = re.fullmatch(r'(-?[\d.,]+)(?:[^\d.,+\-][^+\-]*)?', text)
            if not match:
                raise ValueError('Unknown quantity: ' + repr(value))
            text = match[1]
        if ',' in text and '.' in text:
            if text.rfind('.') > text.rfind(','):
                text = text.replace(',', '')
            else:
                text = text.replace('.', '').replace(',', '.')
        elif re.fullmatch(r'-?\d{1,3}(\.\d{3})+', text):
            text = text.replace('.', '')
        elif re.fullmatch(r'-?\d{1,3}(,\d{3})+', text):
            text = text.replace(',', '')
        else:
            text = text.replace(',', '.')
    try:
        result = Decimal(text) * multiplier
    except InvalidOperation as exc:
        raise ValueError('Unknown numeric format: ' + repr(value)) from exc
    if not result.is_finite():
        raise ValueError('Nonfinite number: ' + repr(value))
    return float(result)


def parse_date(value, sheet='', timestamp=None):
    if value is None or not str(value).strip():
        return iso_date(timestamp)
    year = re.findall(r'\b(20\d{2})\b', sheet)
    result = iso_date(value, int(year[-1]) if year else None)
    if result:
        return result
    text = str(value).strip()
    if re.fullmatch(r'\d{5}(?:\.\d+)?', text) and 20000 <= float(text) <= 80000:
        return (datetime(1899, 12, 30) + timedelta(days=float(text))).date().isoformat()
    match = re.fullmatch(r'(\d{1,2})\s+thg\s+(\d{1,2})\s+(20\d{2})', text)
    if match:
        return datetime(int(match[3]), int(match[2]), int(match[1])).date().isoformat()
    raise ValueError('Unknown date: ' + repr(value))


STATUSES = {
    'cancelled': {'huy', 'da huy', 'huy don', 'da huy don', 'khach huy', 'cancel', 'cancelled', 'khong giao duoc'},
    'returned': {'khach tra', 'tra hang', 'da tra hang', 'tra lai', 'hang tra ve', 'hoan tra', 'da hoan hang', 'doi tra',
                 'returned', 'return'},
    'shipping': {'shipping', 'dang giao', 'dang giao hang', 'dang giao dich', 'dang van chuyen', 'dang ship',
                 'dang gui', 'dang xu ly', 'cho giao'},
    'completed': {'completed', 'hoan thanh', 'da hoan thanh', 'thanh cong', 'xong', 'da xong', 'da lay',
                  'da giao', 'ok'},
}


def status(value):
    # Whole labels only: substrings misread "chuyển" as huỷ and "trạng" as trả.
    text = ' '.join(re.sub(r'[^a-z0-9]+', ' ', name_key(value, accents=True)).split())
    for label, phrases in STATUSES.items():
        if text in phrases:
            return label
    raise ValueError('Unknown order status: ' + repr(value))


def split_toppings(name):
    """'Latte (M) + 2 Kem cheese (25.000đ)' -> ('Latte (M)', [(2, 'Kem cheese', 25000.0)])."""
    parts = str(name or '').split(' + ')
    toppings = []
    while len(parts) > 1:
        match = re.fullmatch(r'(?:(\d+)\s+)?(.+?)\s*\(([\d.,]+\s*(?:k|đ|₫|vnđ)?)\)', parts[-1].strip())
        if not match:
            break
        toppings.insert(0, (int(match[1] or 1), match[2], parse_number(match[3])))
        parts.pop()
    return ' + '.join(parts), toppings


def pick(row, *names):
    return next((row[n] for n in names if n in row and row[n] not in (None, '')), None)


def payment(value):
    text = name_key(value, accents=True)
    if not text:
        return None
    for label, terms in {'debt': ('ghi no', 'debt'), 'cod': ('cod', 'thu ho'),
                         'ewallet': ('vi dien tu', 'ewallet'), 'transfer': ('chuyen khoan', 'transfer'),
                         'cash': ('tien mat', 'cash')}.items():
        if any(t in text for t in terms):
            return label
    raise ValueError('Unknown payment method: ' + repr(value))


def parse_debt_note(note, invoice_total):
    """Extract end-of-data unpaid amount from an order note when README specifies end-of-data note semantics."""
    if note is None or not str(note).strip():
        return 0.0
    low = ' '.join(str(note).split()).casefold()
    plain_note = name_key(note, accents=True)
    if re.search(r'\b(tra du|ck du|tra het|da thanh toan|het no|xong no|thanh toan du)\b', plain_note):
        return 0.0
    if re.search(r'\b(no ca don|no toan bo|chua tra dong nao|ghi no ca don|ghi so ca don)\b', plain_note):
        return max(0.0, float(invoice_total or 0.0))
    num_pat = r'(\d[\d.,]*\s*(?:(?:triệu|trieu|tr|nghìn|nghin|ngàn|ngan|k)\d*|vnđ|vnd|đ|₫)?)'
    owed = re.search(r'(?:còn thiếu|con thieu|thiếu lại|thieu lai|còn nợ|con no|còn lại|con lai|ghi nợ|ghi no|ghi sổ|ghi so|\bnợ|\bno)\s*:?\s*' + num_pat, low)
    if owed:
        return max(0.0, parse_number(owed[1]) or 0.0)
    prepaid = re.search(r'(?:đưa trước|dua truoc|đặt cọc|dat coc|\bcọc|\bcoc|trả trước|tra truoc|ứng trước|ung truoc|\btrả|\btra|\bck)\s*:?\s*' + num_pat, low)
    if prepaid and re.search(r'\b(con lai|ghi no|ghi so|no|thieu)\b', plain_note) and invoice_total is not None:
        return max(0.0, float(invoice_total) - (parse_number(prepaid[1]) or 0.0))
    if re.search(r'\b(chua tra|ghi no|ghi so)\b', plain_note) and invoice_total is not None:
        return max(0.0, float(invoice_total))
    return 0.0


def parse_readme_conversions(readme):
    """Extract explicit product unit conversions documented in README Quy ước."""
    found = []
    for line in readme.splitlines():
        bullet = re.match(r'^\s*-\s+([^:*\n]+?):\s*1\s+([^\d=\n]+?)\s*=\s*([\d.,]+)\s+([^\s;,.:\n]+)', line)
        if bullet:
            found.append((bullet[1].strip(), bullet[2].strip(), parse_number(bullet[3]), bullet[4].strip()))
            continue
        prose = re.match(r'^(?:\s*-\s*)?(?:\*\*[^*]+\*\*:\s*)?([^:\n]+?)\s+bán theo\s+([^\d=,\n]+?),\s*1\s+([^\d=,\n]+?)\s*=\s*([\d.,]+)\s+([^\s;,.:\n]+)', line, flags=re.I)
        if prose and name_key(prose[2]) == name_key(prose[3]):
            large, amt, base = prose[2].strip(), parse_number(prose[4]), prose[5].strip()
            for item_name in prose[1].split(','):
                if item_name.strip():
                    found.append((item_name.strip(), large, amt, base))
    return found


# A few unreadable rows are skipped; more than this means the layout was misread, so the build must not pass.
MAX_REJECTED_ROWS = 3
MAX_REJECTED_SHARE = 0.01

SCHEMA = {
    'products': 'product_id TEXT PRIMARY KEY, product_name TEXT, product_group TEXT, base_unit TEXT, prescription INTEGER, is_service INTEGER, active_ingredient TEXT',
    'product_aliases': 'product_id TEXT, alias TEXT',
    'customers': 'customer_id TEXT PRIMARY KEY, customer_name TEXT, customer_group TEXT',
    'unit_conversions': 'product_id TEXT, sale_unit TEXT, factor REAL, base_unit TEXT',
    'orders': 'order_id TEXT PRIMARY KEY, order_date TEXT, created_at TEXT, status TEXT, customer_id TEXT, payment_method TEXT, invoice_total REAL, lineage TEXT, channel TEXT',
    'order_extras': 'order_id TEXT PRIMARY KEY, debt_left REAL, table_name TEXT, area TEXT, guest_count REAL',
    'order_lines': 'order_id TEXT, line_id TEXT, product_id TEXT, sale_unit TEXT, quantity REAL, basic_quantity REAL, unit_price REAL, basic_unit_price REAL, line_revenue REAL, lineage TEXT',
    'payments': 'payment_id TEXT, order_id TEXT, payment_date TEXT, amount REAL, lineage TEXT',
    'inventory_lots': 'lot_id TEXT PRIMARY KEY, product_id TEXT, import_date TEXT, imported_quantity REAL, lineage TEXT',
    'purchase_lines': 'import_id TEXT, line_id TEXT, product_id TEXT, import_date TEXT, purchase_unit TEXT, quantity REAL, unit_price REAL, recorded_total REAL, source_status TEXT, lineage TEXT',
    'lot_sales': 'order_id TEXT, lot_id TEXT, sold_quantity REAL, returned_quantity REAL, lineage TEXT',
    'stock_report': 'product_id TEXT, on_hand REAL, lineage TEXT',
}

GUIDE = """
NORMALIZED DATA INTERFACE (use these tables, not the source file table names):
Dates are ISO YYYY-MM-DD, numeric columns are REAL/INTEGER: do not parse them again.
orders has one row per order; order_lines has one row per exported item after group inheritance
and within-order deduplication. Never deduplicate orders by customer name. Missing customer_id
means anonymous and must not be ranked. products resolves IDs and unambiguous observed name aliases.
status is exactly completed/cancelled/returned/shipping. A return in lot_sales marks the whole
order returned for sales, even a partial return. sales is the ready-to-use eligible line-level
view: completed/shipping only, with product_name, product_group, prescription, is_service,
customer_id, customer_name, basic_quantity, basic_unit_price, line_revenue, order_date and order_id.
Revenue = SUM(sales.line_revenue), including service fees. Bestseller = SUM(basic_quantity)
grouped by product_id/product_name, excluding is_service=1. Group revenue share denominator
includes the whole shop. Revenue by day/month must aggregate all lines, not rank invoices.
daily_revenue has one row per day with eligible revenue and order_count. Use it for any question
about a day's total, such as counting days with revenue of at least X; never compare line_revenue to X.
Historical selling price comes from order_lines.basic_unit_price joined to orders by date,
including cancelled/returned orders; products is not a historical price source.
Debt uses orders.invoice_total minus SUM(payments.amount) through the requested cutoff;
aggregate payments per order BEFORE joining, clamp each unpaid order to >=0, then sum per customer.
Exclude cancelled and COD orders, retain returned orders. Do not use line revenue for debt.
For source-recorded debt orders payment_method='debt'. Absent payment data is not zero payments.
inventory_lots and lot_sales contain basic-unit quantities. Stock = imported - sold + returned;
cancelled orders never remove stock. Return-event dates are not recorded in these lot exports;
their returned quantities support end-of-data stock, not an inferred historical return date.
unit_conversions.factor is basic units in one sale unit. Basic-unit sales have factor 1.
products.active_ingredient preserves the drug registry's Hoạt chất text; filter this attribute
for ingredient questions, not product-name substrings. Match the exact recorded ingredient: a
compound such as A + B is a separate category, not part of A alone. NULL is unrecorded.
purchase_lines preserves priced import-detail rows, not sales or stock balances. Its quantity
and unit_price use purchase_unit (price per ONE such unit); import_date is the parent import date.
For average purchase price within one requested unit, SUM(quantity*unit_price)/SUM(quantity).
Do not take an unweighted AVG(unit_price). For mixed units, convert quantities with the observed
product_id AND unit conversion first, then express the weighted price per requested unit.
Never use inventory_lots quantities or selling prices as purchase costs. Missing prices/units
are NULL, never zero; incomplete rows cannot silently count as fully priced imports. recorded_total is only the source's recorded amount and may differ from
quantity*unit_price (discounts); source_status is preserved source text, not the orders status enum.
Exclude cancelled import documents using the observed source_status labels, as the rulebook requires.
Calendar week starts Monday. Previous week is the preceding Monday through Sunday, not rolling 7 days.
customers.customer_group is the source's customer group; anonymous orders belong to no group, but a
group's revenue share still divides by the whole shop. orders.channel preserves the recorded sales channel
label: filter on the observed labels listed in the grounding, never infer channel from payment_method.
stock_report.on_hand is stock in basic units at the end of the data only, not at an earlier cutoff.
Source lineage is JSON with raw table/file/sheet/row; originals remain untouched.
Unsupported source roles are explicitly listed below; never infer their existence from an empty table.
If line_items is false the shop records whole invoices only: order_lines has one synthetic row per order
(is_service=1) carrying the order's goods total, so revenue works but product-level questions cannot be answered.
"""


class NormalizedData(BusinessData):
    def __init__(self, raw):
        self.raw = raw
        stat = raw.path.stat()
        digest = hashlib.sha256()
        digest.update(Path(__file__).read_bytes())
        digest.update(Path(roles_module.__file__).read_bytes())
        digest.update(f'{stat.st_size}:{stat.st_mtime_ns}:'.encode())
        digest.update(raw.readme.encode('utf-8'))
        digest.update(json.dumps(raw.tables, sort_keys=True, ensure_ascii=False).encode('utf-8'))
        self.cache = raw.cache / ('normalized-' + digest.hexdigest()[:16])
        self.cache.mkdir(exist_ok=True)
        self.path = self.cache / 'data.sqlite'
        self.meta_path = self.cache / 'schema.json'
        self.report_path = self.cache / 'report.json'
        self.readme_path = self.cache / 'readme.txt'
        if self.path.exists() and self.meta_path.exists() and self.report_path.exists() and self.readme_path.exists():
            self.tables = json.loads(self.meta_path.read_text())
            self.report = json.loads(self.report_path.read_text())
            self.readme = self.readme_path.read_text()
            self.quality = Counter(self.report.get('quality', {}))
        else:
            self.build()
            self.tables = json.loads(self.meta_path.read_text())

    def build(self):
        self.quality = Counter()
        source = sqlite3.connect(self.raw.path.as_uri() + '?mode=ro&immutable=1', uri=True)
        source.row_factory = sqlite3.Row
        tables = {}
        terms = glossary(self.raw.readme)
        for table in self.raw.tables:
            if table['table'] != 'calendar':
                records = [dict(r) for r in source.execute('SELECT * FROM ' + quote(table['table']) + ' ORDER BY rowid')]
                tables[table['table']] = canonicalize(table, records, terms)
        source.close()
        products, customers, orders, order_extras = {}, {}, {}, {}
        product_alias_rows = set()
        lines, conversions, payments, lots, movements, purchases = [], [], [], [], [], []
        goods = {}
        aliases, extra_aliases = {}, {}
        capabilities = set()
        readme_plain = name_key(self.raw.readme, accents=True)
        note_eod_debt = bool(re.search(r'ghi chu[^\n]*(?:no|thieu)[^\n]*(?:cuoi du lieu|ngay cuoi)', readme_plain))
        col_eod_debt = bool(re.search(r'(?:con no|\bno\b|du no|chua thu|con thieu)[^\n]*(?:cuoi du lieu|ngay cuoi)', readme_plain))
        has_debt_snapshot = False
        attempted, rejected, rejected_examples = Counter(), Counter(), []

        def guarded(section, row, table, work):
            # One unreadable row costs that row; finish() decides whether too many were lost.
            attempted[section] += 1
            try:
                work()
            except (ValueError, KeyError) as exc:
                rejected[section] += 1
                if len(rejected_examples) < 20:
                    rejected_examples.append({'section': section, 'error': f'{type(exc).__name__}: {exc}',
                                              'file': row['__dm_file'], 'sheet': row['__dm_sheet'], 'row': row['__dm_row']})

        def too_many(section):
            return rejected[section] > max(MAX_REJECTED_ROWS, attempted[section] * MAX_REJECTED_SHARE)

        def lineage(row, table):
            return json.dumps({'table': table, 'file': row['__dm_file'], 'sheet': row['__dm_sheet'],
                               'row': row['__dm_row']}, ensure_ascii=False)

        def product(identifier, name, group=None, unit=None, alt_names=None):
            if not name and identifier in products:
                return identifier
            key = name_key(name)
            if not identifier:
                matches = aliases.get(name_key(name, accents=True), set()) or extra_aliases.get(name_key(name, accents=True), set())
                if len(matches) == 1:
                    identifier = next(iter(matches))
                elif len(matches) > 1:
                    raise ValueError('Ambiguous product alias: ' + str(name))
                else:
                    identifier = 'name:' + key
            if identifier not in products:
                products[identifier] = [identifier, ' '.join(str(name).split()), group, unit, None,
                                        int(name_key(name, accents=True) == 'phi phuc vu'), None]
            aliases.setdefault(name_key(name, accents=True), set()).add(identifier)
            if group is not None:
                products[identifier][2] = group
            if unit is not None and not products[identifier][3]:
                products[identifier][3] = unit
            if alt_names:
                for part in (p.strip() for p in re.split(r'[,;/|]', str(alt_names)) if p.strip()):
                    product_alias_rows.add((identifier, part))
                    extra_aliases.setdefault(name_key(part, accents=True), set()).add(identifier)
            return identifier

        def customer(identifier, name):
            # A phone copied into the name must not split one customer into two.
            name = re.sub(r'[\s(\-]*0\d[\d .]{8,12}\)?$', '', name) if name else name
            if identifier:
                customers.setdefault(identifier, [identifier, name, None])
                return identifier
            if name and name_key(name, accents=True) not in {'khach le', 'khach vang lai', 'le', 'kl', 'k l', 'khach', 'vang lai', 'khach ngoai'}:
                # Rulebook: every spelling of a name is the same customer.
                identifier = 'name:' + name_key(name, accents=True)
                customers.setdefault(identifier, [identifier, ' '.join(name.split()), None])
                return identifier
            return None

        # Recognize source roles by column signatures, not business or question IDs.
        for table, records in tables.items():
            columns = set(records[0]) if records else set()
            if 'Tên khách hàng' in columns and 'Mã KH' in columns and 'Thành tiền' not in columns:
                for r in records:
                    if pick(r, 'Mã KH'):
                        customer(pick(r, 'Mã KH'), pick(r, 'Tên khách hàng'))
                        if pick(r, 'Nhóm khách'):
                            customers[pick(r, 'Mã KH')][2] = pick(r, 'Nhóm khách')
                            capabilities.add('customer_groups')
            if ('Nhóm hàng hóa' in columns or 'Tên gọi khác' in columns) and 'Thành tiền' not in columns:
                for r in records:
                    if pick(r, 'Mã hàng', 'Tên hàng'):
                        product(pick(r, 'Mã hàng'), pick(r, 'Tên hàng'), pick(r, 'Nhóm hàng hóa'),
                                pick(r, 'Đơn vị cơ bản', 'Đơn vị tính'), pick(r, 'Tên gọi khác'))
            elif ('Thành tiền' not in columns and 'Mã nhập hàng' not in columns and 'Mã hóa đơn' not in columns
                    and 'Hệ số' not in columns and 'Đơn vị cơ bản' in columns
                    and ('Mã hàng' in columns or 'Tên hàng' in columns)):
                for r in records:
                    if pick(r, 'Mã hàng', 'Tên hàng'):
                        product(pick(r, 'Mã hàng'), pick(r, 'Tên hàng'), None, pick(r, 'Đơn vị cơ bản'))
        for table, records in tables.items():
            for r in records:
                if 'Thuốc kê đơn' in r or 'Hoạt chất' in r:
                    pid = pick(r, 'Mã hàng')
                    if pid not in products:
                        raise ValueError('Drug references unknown product: ' + str(pid))
                    if 'Thuốc kê đơn' in r:
                        products[pid][4] = int(name_key(r['Thuốc kê đơn']) in {'true', '1'})
                    ingredient = pick(r, 'Hoạt chất')
                    if ingredient is not None:
                        previous = products[pid][6]
                        if previous is not None and previous != ingredient:
                            raise ValueError('Conflicting active ingredient: ' + str(pid))
                        products[pid][6] = ingredient
                        capabilities.add('active_ingredients')
                factor = pick(r, 'Hệ số')
                if factor is not None:
                    pid = product(pick(r, 'Mã hàng'), pick(r, 'Tên hàng') or
                                  products.get(pick(r, 'Mã hàng'), [None, None])[1])
                    # "6", "15 vỉ" (next unit down) or "1 thùng = 4 bịch; 1 bịch = 10 gói" clauses.
                    clauses = [c.strip() for c in str(factor).split(';') if c.strip()]
                    for clause in clauses:
                        match = re.fullmatch(r'(?:([\d.,]+)\s*([^\d=]+?)\s*=\s*)?([\d.,]+)\s*(.*)', clause)
                        if not match:
                            raise ValueError('Unknown conversion factor: ' + repr(factor))
                        large = match[2] or pick(r, 'Đơn vị quy đổi', 'Đơn vị tính')
                        base = pick(r, 'Đơn vị cơ bản') or (pick(r, 'Đơn vị tính') if match[2] else None)
                        amount = parse_number(match[3]) / (parse_number(match[1]) if match[1] else 1)
                        conversions.append([pid, large, amount, base, name_key(match[4])])
        existing_conv = {(pid, name_key(unit)) for pid, unit, *_ in conversions}
        for item_name, large, amount, base in parse_readme_conversions(self.raw.readme):
            pid = product(None, item_name, unit=base)
            if (pid, name_key(large)) not in existing_conv:
                conversions.append([pid, large, amount, base, name_key(base)])
                existing_conv.add((pid, name_key(large)))
        steps = {(pid, name_key(unit)): (amount, below, name_key(base))
                 for pid, unit, amount, base, below in conversions}

        def to_base(pid, unit, depth=0):
            amount, below, base = steps[(pid, unit)]
            if not below or below == base or depth > 5:
                return amount
            if (pid, below) not in steps:
                raise ValueError('Conversion references unknown unit: ' + str((pid, below)))
            return amount * to_base(pid, below, depth + 1)
        conversions = [[pid, unit, to_base(pid, name_key(unit)), base] for pid, unit, _, base, _ in conversions]
        factors = {(p, name_key(u)): (f, base) for p, u, f, base in conversions}
        for pid, _, _, base in conversions:
            if base:
                products[pid][3] = base

        def prepared(table, records, key, metadata, item=None):
            seen, context, boundary = set(), {}, None
            for raw in records:
                row = raw.copy()
                current = (row['__dm_file'], row['__dm_sheet'])
                if current != boundary:
                    context, boundary = {}, current
                if item and not pick(row, *item):
                    self.quality['non_transaction_rows_skipped'] += 1
                    context = {}
                    continue
                label = name_key(pick(row, *item), accents=True) if item else ''
                if item and (label.startswith(('tong ngay ', 'tong thang', 'ghi chu:')) or label in {'cong', 'tong cong'}
                             or label.startswith(('tong ', 'cong ')) and not pick(row, 'Ngày')):
                    if not pick(row, 'Số lượng'):
                        self.quality['summary_rows_skipped'] += 1
                        context = {}
                        continue
                if key:
                    if row.get(key):
                        if row[key] != context.get(key):
                            context = {key: row[key]}
                    elif context.get(key):
                        row[key] = context[key]
                        self.quality['inherited_order_ids'] += 1
                    else:
                        raise ValueError('Item without order group at ' + lineage(row, table))
                    for column in metadata:
                        if row.get(column) not in (None, ''):
                            context[column] = row[column]
                        else:
                            row[column] = context.get(column)
                    identity = tuple(sorted((k, v) for k, v in row.items() if not k.startswith('__dm_')))
                    if identity in seen:
                        self.quality['duplicate_order_rows_removed'] += 1
                        continue
                    seen.add(identity)
                yield row

        def order(row, oid, table):
            date = parse_date(pick(row, 'Ngày'), row['__dm_sheet'], pick(row, 'Giờ'))
            if not date:
                raise ValueError('Order missing date at ' + lineage(row, table))
            cid = pick(row, 'Mã KH')
            has_ids = 'Mã KH' in row
            cname = pick(row, 'Tên khách hàng')
            cid = customer(cid, None if has_ids else cname)
            # Some exports carry names only on sales rows; fill IDs the customer list did not name.
            if cid and cname and customers[cid][1] is None:
                customers[cid][1] = re.sub(r'[\s(\-]*0\d[\d .]{8,12}\)?$', '', cname)
            if cname is not None or has_ids:
                capabilities.add('customer_identity')
            method = pick(row, 'Phương thức thanh toán')
            if method is not None:
                capabilities.add('payment_method')
            total = parse_number(pick(row, 'Phải thu'))
            if total is None and pick(row, 'Tổng tiền hàng') is not None:
                total = parse_number(pick(row, 'Tổng tiền hàng')) - (parse_number(pick(row, 'Giảm giá HĐ')) or 0)
            if 'Trạng thái' in row:
                label = status(pick(row, 'Trạng thái'))
            else:
                # Without a status column the order note records cancellations; otherwise completed.
                note = name_key(pick(row, 'Ghi chú đơn'), accents=True)
                label = ('cancelled' if re.search(r'\bhuy\b', note) else
                         'returned' if re.search(r'\b(tra lai|tra hang|mang tra|hoan hang|doi tra)\b', note) else
                         'shipping' if re.search(r'\b(dang giao|dang ship|dang gui)\b', note) else 'completed')
            if cid and pick(row, 'Nhóm khách'):
                customers[cid][2] = pick(row, 'Nhóm khách')
                capabilities.add('customer_groups')
            if pick(row, 'Kênh bán'):
                capabilities.add('sales_channels')
            nonlocal has_debt_snapshot
            debt_left = None
            if 'Còn nợ' in row:
                debt_left = 0.0 if label == 'cancelled' else max(0.0, parse_number(pick(row, 'Còn nợ')) or 0.0)
                if col_eod_debt:
                    has_debt_snapshot = True
            elif note_eod_debt and 'Ghi chú đơn' in row:
                debt_left = 0.0 if label == 'cancelled' else parse_debt_note(pick(row, 'Ghi chú đơn'), total)
                has_debt_snapshot = True
            t_name = pick(row, 'Bàn')
            area_val = pick(row, 'Khu vực')
            guests = parse_number(pick(row, 'Số khách'), quantity=True) if pick(row, 'Số khách') is not None else None
            if t_name is not None:
                capabilities.add('tables')
            if guests is not None:
                capabilities.add('guest_count')
            value = [oid, date, pick(row, 'Giờ'), label, cid, payment(method), total, lineage(row, table), pick(row, 'Kênh bán')]
            if any(x is not None for x in (debt_left, t_name, area_val, guests)):
                order_extras.setdefault(oid, [oid, debt_left, t_name, area_val, guests])
            if pick(row, 'Tổng tiền hàng') is not None:
                goods.setdefault(oid, parse_number(pick(row, 'Tổng tiền hàng')))
            if oid in orders and value[3] == 'returned':
                quantity = parse_number(pick(row, 'Số lượng'), quantity=True)
                if quantity is not None and quantity < 0:
                    orders[oid][3] = 'returned'
                    self.quality['dated_return_adjustments'] += 1
                    return
            if oid in orders and orders[oid][:2] + orders[oid][3:7] != value[:2] + value[3:7]:
                raise ValueError('Conflicting order headers: ' + oid)
            orders.setdefault(oid, value)

        def row_id(table, row, records):
            # Row numbers repeat across monthly sheets, so they identify a row only within its sheet.
            multiple = records[0]['__dm_sheet'] != records[-1]['__dm_sheet']
            return table + ':' + (row['__dm_sheet'] + ':' if multiple else '') + row['__dm_row']

        # Separate invoice headers before details; flat line exports handled below.
        headers = set()
        for table, records in tables.items():
            if not records:
                continue
            cols = set(records[0])
            if 'Mã hóa đơn' in cols and ('Phải thu' in cols or 'Tổng tiền hàng' in cols) and 'Số lượng' not in cols:
                for r in prepared(table, records, 'Mã hóa đơn', [], ('Mã hóa đơn',)):
                    guarded('orders', r, table, lambda: order(r, r['Mã hóa đơn'], table))
                    headers.add(r['Mã hóa đơn'])
            elif 'Ngày' in cols and 'Tổng tiền hàng' in cols and 'Số lượng' not in cols:
                # Ticket ledgers (repairs): each dated row is one order with no document number.
                for r in records:
                    if pick(r, 'Ngày') and pick(r, 'Tổng tiền hàng'):
                        guarded('orders', r, table, lambda: order(r, row_id(table, r, records), table))

        item_cols = ('Tên hàng', 'Mã hàng')
        shorthand = {name_key(r['Ghi tắt'], accents=True): r['Tên hàng'] for records in tables.values() for r in records
                     if pick(r, 'Ghi tắt') and pick(r, 'Tên hàng')}
        for table, records in tables.items():
            if not records:
                continue
            cols = set(records[0])
            if not {'Thành tiền', 'Số lượng'} <= cols or not cols & set(item_cols) or 'Mã nhập hàng' in cols:
                continue
            key = 'Mã hóa đơn' if 'Mã hóa đơn' in cols else None
            metadata = [c for c in ('Ngày', 'Trạng thái', 'Tên khách hàng', 'Giờ', 'Ghi chú đơn', 'Kênh bán', 'Nhóm khách', 'Còn nợ', 'Bàn', 'Khu vực', 'Số khách') if c in cols]
            def sale_line(r):
                oid = r[key] if key else row_id(table, r, records)
                quantity = parse_number(r['Số lượng'], quantity=True)
                if quantity is None:
                    # Voucher, points and shipping rows carry no quantity and are not goods revenue.
                    self.quality['non_item_lines_skipped'] += 1
                    return
                if 'Ngày' in cols and oid not in headers:
                    order(r, oid, table)
                if oid not in orders:
                    raise ValueError('Line references unknown order: ' + oid)
                name, toppings = split_toppings(pick(r, 'Tên hàng'))
                name = shorthand.get(name_key(name, accents=True), name)
                pid = product(pick(r, 'Mã hàng'), name or None)
                unit = pick(r, 'Đơn vị tính')
                explicit = pick(r, 'Số lượng quy đổi')
                conversion = factors.get((pid, name_key(unit)))
                basic = parse_number(explicit, quantity=True) if explicit is not None else quantity * (conversion[0] if conversion else 1)
                if products[pid][3] and not conversion and explicit is None and name_key(unit) != name_key(products[pid][3]):
                    raise ValueError('Unknown unit conversion: ' + str((pid, unit)))
                price = parse_number(pick(r, 'Đơn giá'))
                revenue = parse_number(pick(r, 'Thành tiền'))
                if revenue is None or basic is None:
                    raise ValueError('Missing line amount or basic quantity')
                # Rulebook: a topping sharing the cell is its own item; its money leaves the dish.
                for index, (count, topping, amount) in enumerate(toppings, 1):
                    revenue -= amount
                    known = name_key('topping ' + topping, accents=True) in aliases
                    tid = product(None, ('Topping ' if known else '') + topping)
                    lines.append([oid, (pick(r, 'STT') or r['__dm_row']) + '+' + str(index), tid, None, count, count,
                                  amount / count, amount / count, amount, lineage(r, table)])
                lines.append([oid, pick(r, 'STT') or r['__dm_row'], pid, unit, quantity, basic,
                              price, price * quantity / basic if basic and price is not None else None, revenue, lineage(r, table)])

            for r in prepared(table, records, key, metadata, item_cols):
                guarded('lines', r, table, lambda: sale_line(r))

        for table, records in tables.items():
            for r in records:
                if 'Mã GD' in r and 'Mã hóa đơn' in r:
                    capabilities.add('payments')
                    guarded('payments', r, table, lambda: payments.append([pick(r, 'Mã GD'), pick(r, 'Mã hóa đơn'),
                            parse_date(pick(r, 'Thời điểm')), parse_number(pick(r, 'Tiền')), lineage(r, table)]))
                if 'Số lượng trả' in r:
                    capabilities.add('lot_sales')
                    guarded('lot_sales', r, table, lambda: movements.append([pick(r, 'Mã hóa đơn'), pick(r, 'Mã lô'),
                            parse_number(pick(r, 'Số lượng quy đổi', 'Số lượng')),
                            parse_number(pick(r, 'Số lượng trả')), lineage(r, table)]))
        # Lot import dates come from their parent import header.
        import_dates, import_statuses = {}, {}
        for records in tables.values():
            for r in records:
                iid = pick(r, 'Mã nhập hàng')
                date = pick(r, 'Ngày nhập')
                if iid and date:
                    def import_header(r=r, iid=iid, date=date):
                        import_dates[iid] = parse_date(date, r['__dm_sheet'])
                        import_statuses[iid] = pick(r, 'Trạng thái')
                    guarded('purchases', r, 'import', import_header)
        for table, records in tables.items():
            for r in records:
                if {'Số lượng nhập', 'Mã hàng', 'Mã lô', 'Mã nhập hàng'} <= set(r):
                    capabilities.add('inventory_lots')
                    guarded('inventory_lots', r, table, lambda: lots.append([pick(r, 'Mã lô'), pick(r, 'Mã hàng'),
                            import_dates[pick(r, 'Mã nhập hàng')], parse_number(pick(r, 'Số lượng nhập')), lineage(r, table)]))
        # Priced imports have a distinct source signature; inventory quantities are not costs.
        for table, records in tables.items():
            for r in records:
                parent_id = pick(r, 'Mã nhập hàng')
                price_col = next((c for c in ('Giá nhập', 'Đơn giá') if c in r), None)
                quantity_col = next((c for c in ('SL nhập', 'Số lượng') if c in r), None)
                if not ('Mã nhập hàng' in r and price_col and quantity_col):
                    continue

                def purchase(r=r, parent_id=parent_id, price_col=price_col, quantity_col=quantity_col):
                    identifier = pick(r, 'Mã hàng')
                    name = pick(r, 'Tên hàng')
                    if identifier not in products and not name:
                        raise ValueError('Purchase references unknown product: ' + str(identifier))
                    pid = product(identifier, name)
                    date = import_dates.get(parent_id) or parse_date(pick(r, 'Ngày nhập'), r['__dm_sheet'])
                    if not date:
                        raise ValueError('Purchase missing import date: ' + lineage(r, table))
                    price = parse_number(pick(r, price_col))
                    purchases.append([parent_id, pick(r, 'STT') or r['__dm_row'], pid, date, pick(r, 'Đơn vị tính'),
                        parse_number(pick(r, quantity_col), quantity=True), price, parse_number(pick(r, 'Thành tiền')),
                        import_statuses.get(parent_id), lineage(r, table)])
                    if price is not None:
                        capabilities.add('purchase_prices')
                guarded('purchases', r, table, purchase)
        # A stock report has one row per product; a running stock ledger repeats products and is not a snapshot.
        stock = []
        for table, records in tables.items():
            if records and 'Tồn kho' in records[0] and len({pick(r, 'Mã hàng', 'Tên hàng') for r in records}) == len(records):
                for r in records:
                    if pick(r, 'Mã hàng') in products or pick(r, 'Tên hàng'):
                        guarded('stock_report', r, table, lambda: stock.append([product(pick(r, 'Mã hàng'), pick(r, 'Tên hàng')),
                                parse_number(pick(r, 'Tồn kho'), quantity=True), lineage(r, table)]))
        if stock:
            capabilities.add('stock_report')
        if has_debt_snapshot and 'payments' not in capabilities:
            capabilities.add('debt_snapshot')
        for section in ('orders', 'lines'):
            if too_many(section):
                raise ValueError(f'Too many unreadable {section}: {rejected[section]} of {attempted[section]}; '
                                 f'first: {rejected_examples[0]["error"]}')
        # Supporting tables are reported as absent rather than risk the sales data with them.
        dropped = []
        for section, rows, roles in [('payments', payments, ['payments']), ('lot_sales', movements, ['lot_sales']),
                                     ('inventory_lots', lots, ['inventory_lots']), ('stock_report', stock, ['stock_report']),
                                     ('purchases', purchases, ['purchase_prices'])]:
            if too_many(section):
                rows.clear()
                capabilities.difference_update(roles)
                dropped.append(section)
        if lines:
            capabilities.add('line_items')
        else:
            # Invoice-only exports: one synthetic line per order carries its goods total (rulebook revenue).
            pid = product('order-total', 'Tiền hàng của đơn (không có chi tiết mặt hàng)')
            products[pid][5] = 1
            for oid in orders:
                if goods.get(oid) is not None:
                    lines.append([oid, '1', pid, None, None, None, None, None, goods[oid], orders[oid][7]])
        returned = {r[0] for r in movements if r[3] and r[3] > 0}
        for oid in returned:
            if oid not in orders:
                raise ValueError('Lot sale references missing order: ' + oid)
            if orders[oid][3] != 'cancelled':
                orders[oid][3] = 'returned'
        values = {'products': list(products.values()), 'product_aliases': sorted(product_alias_rows),
                  'customers': list(customers.values()), 'unit_conversions': conversions,
                  'orders': list(orders.values()), 'order_extras': list(order_extras.values()),
                  'order_lines': lines, 'payments': payments, 'inventory_lots': lots,
                  'lot_sales': movements, 'purchase_lines': purchases, 'stock_report': stock}
        if not lines:
            raise ValueError('No supported sales schema found')
        # One temporary file per process, so two runs sharing a cache never rename each other's half-built file.
        temporary = self.path.with_suffix(f'.building.{os.getpid()}.sqlite')
        temporary.unlink(missing_ok=True)
        db = sqlite3.connect(temporary)
        try:
            for name, ddl in SCHEMA.items():
                db.execute('CREATE TABLE ' + name + ' (' + ddl + ')')
                if values[name]:
                    db.executemany('INSERT INTO ' + name + ' VALUES (' + ','.join('?' for _ in values[name][0]) + ')', values[name])
            for table, column in [('order_lines', 'order_id'), ('payments', 'order_id'), ('lot_sales', 'lot_id')]:
                db.execute(f'CREATE INDEX {table}_{column} ON {table}({column})')
            db.execute('''CREATE VIEW sales AS SELECT l.*, o.order_date, o.status, o.customer_id,
                c.customer_name, c.customer_group, o.channel, o.payment_method, p.product_name, p.product_group, p.base_unit,
                p.prescription, p.is_service, e.table_name, e.area, e.guest_count FROM order_lines l JOIN orders o USING(order_id)
                LEFT JOIN order_extras e USING(order_id)
                JOIN products p USING(product_id) LEFT JOIN customers c USING(customer_id)
                WHERE o.status IN ('completed','shipping')''')
            db.execute('''CREATE VIEW daily_revenue AS SELECT order_date, SUM(line_revenue) AS revenue,
                COUNT(DISTINCT order_id) AS order_count FROM sales GROUP BY order_date''')
            metadata = []
            for name in [*SCHEMA, 'sales', 'daily_revenue']:
                info = list(db.execute('PRAGMA table_info(' + name + ')'))
                columns = [r[1] for r in info]
                metadata.append({'table': name, 'columns': columns, 'types': {r[1]: r[2] for r in info},
                                 'rows': db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]})
            db.commit()
        finally:
            db.close()
        temporary.replace(self.path)
        self.meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        self.readme = self.raw.readme + '\n' + GUIDE + '\nSOURCE CAPABILITIES:\n' + json.dumps({
            c: c in capabilities for c in ('line_items', 'customer_identity', 'payment_method', 'payments', 'debt_snapshot', 'lot_sales', 'inventory_lots', 'active_ingredients', 'purchase_prices', 'customer_groups', 'sales_channels', 'stock_report', 'tables', 'guest_count')})
        self.quality.update({'orders': len(orders), 'lines': len(lines), 'products': len(products), 'lot_return_orders': len(returned)})
        self.report = {'quality': dict(self.quality), 'source_capabilities': sorted(capabilities),
                       'rejected_rows': dict(rejected), 'rejected_examples': rejected_examples, 'dropped_sections': dropped,
                       'raw_database': str(self.raw.path), 'normalized_database': str(self.path),
                       'schema_adapter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        self.report_path.write_text(json.dumps(self.report, ensure_ascii=False, indent=2))
        self.readme_path.write_text(self.readme)
