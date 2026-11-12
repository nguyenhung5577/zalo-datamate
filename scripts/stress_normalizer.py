"""No-model stress test: rewrite a business's layout or values, rebuild the normalizer, compare with its unchanged build.

A result only says the build matches the unchanged build. It does not check that either one is correct.
Usage: stress_normalizer.py <output.json> [--workers N] [--business biz_101 ...] [--scenario name ...]
"""
import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from datamate.data import BusinessData
from datamate.normalized import NormalizedData, parse_date, parse_number
from datamate.roles import NAME_ROLES, column_roles, glossary, key

CACHE = ROOT / '.cache/normalize-survey/train-cache'
ENGLISH = {
    'order_id': 'Order ID', 'order_date': 'Order Date', 'order_time': 'Order Time', 'status': 'Status',
    'customer_id': 'Customer ID', 'customer_name': 'Customer Name', 'payment_method': 'Payment Method',
    'invoice_total': 'Invoice Total', 'goods_total': 'Goods Total', 'order_discount': 'Order Discount',
    'customer_group': 'Customer Segment', 'channel': 'Sales Channel', 'stock_on_hand': 'Stock On Hand',
    'product_id': 'Product Code', 'product_name': 'Product Name', 'product_group': 'Category', 'unit': 'Unit',
    'base_unit': 'Base Unit', 'large_unit': 'Pack Unit', 'factor': 'Conversion Factor', 'quantity': 'Quantity',
    'basic_quantity': 'Base Quantity', 'unit_price': 'Unit Price', 'line_amount': 'Line Amount', 'line_no': 'Line No',
    'ingredient': 'Active Ingredient', 'prescription': 'Prescription Required', 'payment_id': 'Payment ID',
    'payment_date': 'Payment Date', 'payment_amount': 'Paid Amount', 'import_id': 'Purchase ID',
    'import_date': 'Purchase Date', 'purchase_price': 'Purchase Price', 'purchase_quantity': 'Purchase Quantity',
    'lot_id': 'Lot No', 'imported_quantity': 'Received Quantity', 'returned_quantity': 'Returned Quantity',
}
# Plausible Vietnamese headers that the name fallback has never seen.
SYNONYMS = {
    'order_id': 'Số đơn bán', 'order_date': 'Ngày phát sinh', 'order_time': 'Giờ phát sinh', 'status': 'Tình hình đơn',
    'customer_id': 'Mã người mua hàng', 'customer_name': 'Người mua', 'payment_method': 'Cách trả tiền',
    'invoice_total': 'Tổng cần thu', 'goods_total': 'Tổng giá trị hàng', 'order_discount': 'Khuyến mãi đơn',
    'customer_group': 'Phân khúc khách', 'channel': 'Nơi bán', 'stock_on_hand': 'Còn trong kho',
    'product_id': 'Mã vật tư', 'product_name': 'Tên vật tư', 'product_group': 'Ngành hàng', 'unit': 'Quy cách',
    'base_unit': 'Đơn vị nhỏ nhất', 'large_unit': 'Đơn vị đóng gói', 'factor': 'Số quy đổi', 'quantity': 'Số lượng mua',
    'basic_quantity': 'Số lượng đơn vị nhỏ', 'unit_price': 'Giá tiền', 'line_amount': 'Doanh số dòng', 'line_no': 'Dòng số',
    'ingredient': 'Dược chất', 'prescription': 'Cần đơn thuốc', 'payment_id': 'Số phiếu thu', 'payment_date': 'Ngày nộp tiền',
    'payment_amount': 'Số tiền nộp', 'import_id': 'Số chứng từ nhập', 'import_date': 'Ngày về hàng',
    'purchase_price': 'Giá vốn nhập', 'purchase_quantity': 'Khối lượng nhập', 'lot_id': 'Số lô hàng',
    'imported_quantity': 'Lượng nhập kho', 'returned_quantity': 'Lượng trả về',
}
assert not {key(v) for v in SYNONYMS.values()} & set(NAME_ROLES)


def plain(name):
    text = unicodedata.normalize('NFD', name.replace('đ', 'd').replace('Đ', 'D'))
    return '_'.join(''.join(c for c in text if unicodedata.category(c) != 'Mn').split())


def translated(names):
    return lambda column, role, index: names.get(role, column)


# name: (rename(column, role, index) or None, drop README, reverse order, {role: rewrite(value)}, change one cell only)
SCENARIOS = {
    'reordered': (None, False, True, {}, False),
    'upper_headers': (lambda c, r, i: c.upper(), False, False, {}, False),
    'no_accent_headers': (lambda c, r, i: plain(c), False, False, {}, False),
    'no_readme': (None, True, False, {}, False),
    'synonyms_with_readme': (translated(SYNONYMS), False, False, {}, False),
    'synonyms_no_readme': (translated(SYNONYMS), True, False, {}, False),
    'english_with_readme': (translated(ENGLISH), False, False, {}, False),
    'english_no_readme': (translated(ENGLISH), True, False, {}, False),
    'opaque_no_readme': (lambda c, r, i: f'col_{i}', True, False, {}, False),
    'bad_status_cell': (None, False, False, {'status': lambda v: 'Chờ xác nhận'}, True),
    'bad_date_cell': (None, False, False, {'order_date': lambda v: 'không rõ'}, True),
    'bad_amount_cell': (None, False, False, {'line_amount': lambda v: 'n/a'}, True),
    'bad_payment_cell': (None, False, False, {'payment_method': lambda v: 'Thẻ quà tặng'}, True),
    'upper_status_values': (None, False, False, {'status': lambda v: v.upper() if v else v}, False),
    'date_format': (None, False, False, {'order_date': lambda v: day_first(v)}, False),
    'amount_format': (None, False, False, {'line_amount': lambda v: dotted(v), 'unit_price': lambda v: dotted(v)}, False),
}


def day_first(value):
    # A short date like 1/1 takes its year from the sheet name; leave what cannot be read alone.
    try:
        year, month, day = parse_date(value)[:10].split('-')
    except (ValueError, AttributeError, TypeError):
        return value
    return f'{day}/{month}/{year}'


def dotted(value):
    try:
        return f'{round(parse_number(value)):,}đ'.replace(',', '.')
    except (ValueError, TypeError):
        return value


def perturb(raw, scenario, folder):
    """Write a changed copy of the raw import into folder and return an object NormalizedData accepts."""
    rename, drop_readme, reverse, rewrites, one_cell = SCENARIOS[scenario]
    terms, readme, tables, touched = glossary(raw.readme), raw.readme, [], 0
    source = sqlite3.connect(raw.path.as_uri() + '?mode=ro&immutable=1', uri=True)
    target = sqlite3.connect(folder / 'data.sqlite')
    for table in reversed(raw.tables) if reverse else raw.tables:
        hidden = [c for c in table['columns'] if c.startswith('__dm_')]
        visible = [c for c in table['columns'] if not c.startswith('__dm_')]
        rows = source.execute('SELECT ' + ','.join('"' + c.replace('"', '""') + '"' for c in visible + hidden)
                              + f' FROM "{table["table"]}"').fetchall()
        roles = column_roles(table, visible, terms) if table['table'] != 'calendar' else {}
        names = {c: c for c in visible}
        if rename and table['table'] != 'calendar':
            names = {c: rename(c, roles.get(c), i) for i, c in enumerate(visible)}
            # Two source columns can translate to one name; keep them distinct, as the importer does.
            seen = set()
            for c in visible:
                while names[c].casefold() in seen:
                    names[c] += '_'
                seen.add(names[c].casefold())
                readme = readme.replace(f'**{c}**', f'**{names[c]}**').replace(f'.{c}`', f'.{names[c]}`')
        indexes = {i: rewrites[roles[c]] for i, c in enumerate(visible) if roles.get(c) in rewrites}
        if indexes:
            filled = [n for n, row in enumerate(rows) if any(row[i] for i in indexes)]
            chosen = {filled[len(filled) // 2]} if one_cell and filled and not touched else set() if one_cell else None
            if chosen is None or chosen:
                touched += 1
                rows = [tuple(indexes[i](v) if i in indexes and (chosen is None or n in chosen) else v
                              for i, v in enumerate(row)) for n, row in enumerate(rows)]
        order = visible[::-1] + hidden if reverse else visible + hidden
        positions = [(visible + hidden).index(c) for c in order]
        columns = [names.get(c, c) for c in order]
        target.execute(f'CREATE TABLE "{table["table"]}" (' + ','.join('"' + c.replace('"', '""') + '" TEXT' for c in columns) + ')')
        target.executemany(f'INSERT INTO "{table["table"]}" VALUES (' + ','.join('?' * len(columns)) + ')',
                           [[row[p] for p in positions] for row in rows])
        tables.append({**table, 'columns': columns})
    target.commit()
    target.close()
    source.close()
    if rewrites and not touched:
        return None
    return SimpleNamespace(cache=folder, path=folder / 'data.sqlite', readme='' if drop_readme else readme, tables=tables)


def fingerprint(data):
    db = sqlite3.connect(data.path)
    one = lambda sql: db.execute(sql).fetchone()[0]
    result = {name: one(f'SELECT COUNT(*) FROM {name}') for name in
              ('orders', 'order_lines', 'products', 'customers', 'unit_conversions', 'payments', 'inventory_lots',
               'lot_sales', 'purchase_lines', 'stock_report')}
    result['revenue'] = round(one('SELECT COALESCE(SUM(line_revenue), 0) FROM sales'), 2)
    result['months'] = {m: round(v, 2) for m, v in db.execute(
        'SELECT substr(order_date, 1, 7), SUM(line_revenue) FROM sales GROUP BY 1')}
    result['status'] = dict(db.execute('SELECT status, COUNT(*) FROM orders GROUP BY 1'))
    result['known'] = {c: one(f'SELECT COUNT({c}) FROM orders') for c in
                       ('customer_id', 'payment_method', 'invoice_total', 'channel')}
    result['capabilities'] = data.report['source_capabilities']
    db.close()
    return result


def compare(base, other):
    """same: every figure matches. minor: revenue within 0.1% and the same capabilities. changed: anything else."""
    differences = [k for k in base if base[k] != other[k]]
    if not differences:
        return 'same', differences, 0.0
    drift = abs(other['revenue'] - base['revenue']) / max(abs(base['revenue']), 1) * 100
    small = drift <= 0.1 and base['capabilities'] == other['capabilities'] and abs(other['orders'] - base['orders']) <= 1
    return 'minor' if small else 'changed', differences, round(drift, 3)


def run_business(job):
    business, scenarios = job
    raw = BusinessData(ROOT / 'train', business, CACHE)
    with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as scratch:
        unchanged = perturb_free(raw, Path(scratch))
    records = []
    for scenario in scenarios:
        record = {'business': business, 'scenario': scenario}
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as scratch:
            try:
                changed = perturb(raw, scenario, Path(scratch))
            except Exception as exc:
                changed = False
                record.update(outcome='script_error', error=f'{type(exc).__name__}: {exc}'[:200])
            if changed is False:
                pass
            elif changed is None:
                record['outcome'] = 'not_applicable'
            else:
                try:
                    other = fingerprint(NormalizedData(changed))
                except Exception as exc:
                    record.update(outcome='failed', error=f'{type(exc).__name__}: {exc}'[:200])
                else:
                    outcome, differences, drift = compare(unchanged, other)
                    record.update(outcome=outcome, differences=differences, revenue_drift_percent=drift)
        records.append(record)
        print(business, scenario, record['outcome'], record.get('error', record.get('differences', '')), flush=True)
    return unchanged, records


def perturb_free(raw, folder):
    return fingerprint(NormalizedData(SimpleNamespace(cache=folder, path=raw.path, readme=raw.readme, tables=raw.tables)))


def new_type(folder, tables, readme=''):
    """A small package of a kind no train business has, written directly as raw import tables."""
    db = sqlite3.connect(folder / 'data.sqlite')
    described = []
    for name, (columns, rows) in tables.items():
        full = columns + ['__dm_file', '__dm_sheet', '__dm_row']
        db.execute(f'CREATE TABLE "{name}" (' + ','.join(f'"{c}" TEXT' for c in full) + ')')
        db.executemany(f'INSERT INTO "{name}" VALUES (' + ','.join('?' * len(full)) + ')',
                       [list(r) + [name + '.csv', '', str(i)] for i, r in enumerate(rows, 2)])
        described.append({'table': name, 'source': name + '.csv', 'sheet': '', 'columns': full, 'rows': len(rows)})
    db.commit()
    db.close()
    return SimpleNamespace(cache=folder, path=folder / 'data.sqlite', readme=readme, tables=described)


NEW_TYPES = {
    'gradebook': {'diem': (['Mã HS', 'Họ tên', 'Lớp', 'Môn', 'Điểm', 'Ngày kiểm tra'],
                           [('HS1', 'An', '6A', 'Toán', '8.5', '2026-03-01'), ('HS2', 'Bình', '6A', 'Văn', '7', '2026-03-02')])},
    'clinic_visits': {'luot_kham': (['Mã lượt', 'Ngày khám', 'Bệnh nhân', 'Bác sĩ', 'Chẩn đoán', 'Phí khám'],
                                    [('L1', '2026-03-01', 'An', 'BS Hà', 'Cảm', '150000'), ('L2', '2026-03-02', 'Bình', 'BS Hà', 'Ho', '150000')])},
    'room_rental': {'phieu_thu': (['Số phòng', 'Người thuê', 'Tháng', 'Tiền phòng', 'Tiền điện', 'Đã đóng'],
                                  [('101', 'An', '2026-03', '3000000', '200000', 'Rồi'), ('102', 'Bình', '2026-03', '2800000', '150000', 'Chưa')])},
    'employee_payroll': {'luong': (['Mã NV', 'Họ tên', 'Phòng ban', 'Tháng', 'Lương cơ bản', 'Thưởng'],
                                   [('NV1', 'An', 'Kho', '2026-03', '9000000', '500000'), ('NV2', 'Bình', 'Bán hàng', '2026-03', '8000000', '0')])},
}


def run_new_types():
    records = []
    for name, tables in NEW_TYPES.items():
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as scratch:
            try:
                fingerprint(NormalizedData(new_type(Path(scratch), tables)))
                records.append({'type': name, 'outcome': 'built'})
            except Exception as exc:
                records.append({'type': name, 'outcome': 'failed', 'error': f'{type(exc).__name__}: {exc}'[:200]})
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--business', nargs='*')
    parser.add_argument('--scenario', nargs='*', default=list(SCENARIOS))
    args = parser.parse_args()
    businesses = args.business or sorted(p.name for p in (ROOT / 'train/data').iterdir())
    with ProcessPoolExecutor(args.workers) as pool:
        done = list(pool.map(run_business, [(b, args.scenario) for b in businesses]))
    results = {'unchanged': {b: d[0] for b, d in zip(businesses, done)},
               'records': [r for d in done for r in d[1]], 'new_types': run_new_types()}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print('wrote', args.output)


if __name__ == '__main__':
    main()
