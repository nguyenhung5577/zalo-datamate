"""Rulebook metrics computed by fixed SQL: the model only fills slots, so definitions cannot drift."""
import calendar
from datetime import date, timedelta
import math
import re


class Unsupported(ValueError):
    """The slots fall outside this layer; the caller must use another method."""


def months_back(day, count=1):
    index = day.year * 12 + day.month - 1 - count
    return date(index // 12, index % 12 + 1, 1)


def month_range(year, month):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def resolve_period(period, as_of):
    """Return inclusive (start, end) dates following rulebook section 2."""
    today = date.fromisoformat(as_of)
    kind = period.get('kind')
    if kind == 'today':
        return today, today
    if kind == 'yesterday':
        return today - timedelta(1), today - timedelta(1)
    if kind == 'day':
        day = date.fromisoformat(period['date'])
        return day, day
    if kind == 'range':
        return date.fromisoformat(period['start']), date.fromisoformat(period['end'])
    if kind == 'until':
        return date.min, date.fromisoformat(period['date'])
    if kind == 'until_today':
        return date.min, today
    if kind == 'month':
        year = period.get('year')
        if year is None:
            year = today.year if date(today.year, period['month'], 1) <= today else today.year - 1
        return month_range(year, period['month'])
    if kind == 'quarter':
        year = period.get('year')
        first = 3 * period['quarter'] - 2
        if year is None:
            year = today.year if date(today.year, first, 1) <= today else today.year - 1
        return date(year, first, 1), month_range(year, first + 2)[1]
    if kind == 'year':
        return date(period['year'], 1, 1), date(period['year'], 12, 31)
    if kind == 'this_month':
        return today.replace(day=1), today
    if kind == 'last_month':
        start = months_back(today)
        return start, month_range(start.year, start.month)[1]
    if kind == 'this_week':
        return today - timedelta(today.weekday()), today
    if kind == 'last_week':
        monday = today - timedelta(today.weekday())
        return monday - timedelta(7), monday - timedelta(1)
    if kind == 'last_days':
        return today - timedelta(period['days'] - 1), today
    if kind == 'year_to_date':
        return today.replace(month=1, day=1), today
    raise Unsupported('period ' + repr(kind))


def previous_period(period, start, end, against=None):
    """The comparison period: this month compares with the same days of last month."""
    kind = period.get('kind')
    if against == 'last_year':
        return start.replace(year=start.year - 1, day=1) if start.day == 1 else start.replace(year=start.year - 1), \
            month_range(end.year - 1, end.month)[1] if kind == 'month' else end.replace(year=end.year - 1, day=min(end.day, 28))
    if kind in {'month', 'last_month'}:
        first = months_back(start)
        return first, month_range(first.year, first.month)[1]
    if kind == 'this_month':
        first = months_back(start)
        last_day = calendar.monthrange(first.year, first.month)[1]
        return first, first.replace(day=min(end.day, last_day))
    if kind == 'quarter':
        first = months_back(start, 3)
        return first, start - timedelta(1)
    days = (end - start).days + 1
    return start - timedelta(days), start - timedelta(1)


def money(value):
    return math.floor(value + 0.5)


def two(value):
    return round(value + 0.0, 2)


SHIFTS = {'morning': (6, 13), 'afternoon': (14, 21)}
SLOTS = {'shift', 'channel', 'payment', 'product_group', 'product', 'customer_group', 'customer'}
SLOT_REQUEST = """Map the Vietnamese shop question to one JSON object. Questions may use slang, typos or no accents.
{"metric": ..., "period": {...}, "filters": {...}, "top": k, "of": "revenue|orders", "against": "last_year"}
Leave out optional fields that do not apply.
metric, one of:
revenue: total revenue or money sold; for two channels or two payment ways asked at once, give both as a list in filters, in the order asked.
orders: number of orders. average_order: average value per order.
cancel_rate: percent of orders cancelled. cancelled: number of cancelled orders.
growth: percent change against the previous period; "of" says which number; "against" only for the same period last year.
shift_gap: percent gap between afternoon and morning shift; "of" says which number.
share: percent of whole-shop revenue held by the filters. top_products: best selling items by quantity; "of": "revenue" when ranked by money.
top_customers: customers who spent the most; "of": "orders" when ranked by number of orders. "top" is k when several are asked for; leave it out for just one.
top_debtors: the k customers owing the most at the end of a day, with the amounts; "top" is k.
purchases: total spent on imports. debt_collected: debt repaid in the period. debt_total: total debt still owed.
debtors: number of customers still owing.
units: how many units of one product (filters.product) or one product_group were sold.
best_day: which day had the highest revenue; "of": "orders" when it asks for the most orders.
count_over: how many days, orders, customers or items reach an amount; "count" is "days", "orders", "customers" or "items".
Days and orders reach a revenue amount; customers and items reach a number of orders. Also the percent of buying customers that reach it.
unsold: how many items sold before the period sold nothing in it.
price: the selling price of one product (filters.product) on one day. buy_price: the average import price of one product per unit.
monthly: revenue of each month listed, in order. rising: whether revenue rose every month over the months listed.
best_month: which of the months listed had the highest revenue.
customer_debt: a named customer's new debt and debt repaid in the period, as two numbers; filters.customer is the name.
price_effect: percent change in units sold of one product (filters.product) in the N days from a price change day against the N days before.
stock_left: how much of one product (filters.product) is left in stock at the end of a day.
gross_profit: the shop's gross profit (revenue less the cost of goods sold), or which item made the most gross profit.
net_profit: the shop's net profit after operating expenses (lai rong / loi nhuan rong).
stock_value: total inventory value at cost (hang ton kho tinh theo gia von) at the end of a day.
import_minus_sold: base units imported minus base units sold of one product (filters.product) in a period.
staff_sales: money sold by one staff member (named or by code), or which staff member sold the most money.
top_supplier: which supplier the shop imported the most money from, or its main supplier.
units_total: how many units of one kind (ly, goi, lon, cai...) were sold across all items, maybe of one size; no product named.
debt_age: debt still owed at the end of a day split by debt age (0-7, 8-15, over 15 days), or owed on orders older than N days.
other: anything else, including any condition not listed here: a named customer's other numbers,
other kinds of several numbers, listing, lowest, expiry, ingredients, Tet.
When unsure, use other. Return {"metric":"other"} with nothing else.
period, one of: {"kind":"today"} {"kind":"yesterday"} {"kind":"day","date":"YYYY-MM-DD"} {"kind":"range","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}
{"kind":"until","date":"YYYY-MM-DD"} (remaining balance at the end of a day) {"kind":"until_today"}
{"kind":"month","month":M,"year":Y} (leave out year if not written) {"kind":"quarter","quarter":Q,"year":Y} {"kind":"year","year":Y}
{"kind":"this_month"} {"kind":"last_month"} {"kind":"this_week"} {"kind":"last_week"} {"kind":"last_days","days":N} {"kind":"year_to_date"}
filters may use: shift ("morning" or "afternoon"), channel, payment, product_group, customer_group, each exactly as listed below,
and product, the exact product name, or customer, the name as written. Do not compute dates relative to today yourself except inside range.
Use a filter only when the question names it. Examples:
"thang 5/2025 bi huy may don" -> {"metric":"cancelled","period":{"kind":"month","month":5,"year":2025}}
"Cho toi biet so don hqua" -> {"metric":"orders","period":{"kind":"yesterday"}}
"so bill hom nay la bao nhieu" -> {"metric":"orders","period":{"kind":"today"}}
"quy 2/2025 so don la bn" -> {"metric":"orders","period":{"kind":"quarter","quarter":2,"year":2025}}
"ngay nao doanh thu cao nhat thang 7" -> {"metric":"best_day","period":{"kind":"month","month":7}}
"thang 3/2025 co may don tu 500.000 dong tro len" -> {"metric":"count_over","count":"orders","period":{"kind":"month","month":3,"year":2025}}
"top 5 khach no nhieu nhat den het hom nay" -> {"metric":"top_debtors","top":5,"period":{"kind":"until_today"}}
"doanh thu thang 4, 5, 6/2025 lan luot la bao nhieu" -> {"metric":"monthly","period":{"kind":"month","month":6,"year":2025}}
"tien no cua khach Le Van Nam thang nay" -> {"metric":"other"}
"doanh thu don tien mat va don COD thang 4/2025 lan luot" -> {"metric":"revenue","period":{"kind":"month","month":4,"year":2025},"filters":{"payment":["cash","cod"]}}
"thang 2/2025 khach Tran Thu Ha no them bao nhieu, tra duoc bao nhieu" -> {"metric":"customer_debt","period":{"kind":"month","month":2,"year":2025},"filters":{"customer":"Tran Thu Ha"}}
"""


OUTSIDE = (r'lan luot|moi loai|moi thang|tung thang|lien tiep|lien nhau|lien tuc|xu huong|liet ke|lai gop|lai rong|loi nhat|doi gia|tang gia|len gia'
           r'|ke tu|tuoi no|(?:hon|qua) \d+ ngay|nhan vien|\bdot\b|tro len|tren \d|duoi \d|\btet\b|moi ben|nha cung cap|\bncc\b|phi ship|phi giao|voucher|\bdiem\b|may cu|tra gop|bao hanh|\btho\b|bien so|\bluot\b|gia von|\blen \d')
# Supplier words from the rulebook's "nha cung cap" and its common synonyms, not from any one shop's questions.
SUPPLIER = r'nha cung cap|\bncc\b|nha phan phoi|\bnpp\b|ben cung cap|cong ty cung cap|nguon (?:nhap|hang)'
UNITS = r'(?:may|bao nhieu|bao nhiu|\bbn|bnhiu|nhieu) (?:ly|cai|goi|cay|chai|hop|thung|bao|kg|lon|vien|tui|phan(?! tram)|to|dia|mon)\b'
METRIC_EVIDENCE = {
    'revenue': r'doanh thu|dthu|tien|revenue',
    'orders': r'\bdon\b|\bdh\b|\bbill\b|hoa don|\bo do\b|phieu sua',
    'debt_collected': r'thu hoi|tra no|thu no|cong no|\bno\b|mua chiu',
    'debt_total': r'\bno\b|cong no',
    'debtors': r'\bno\b|cong no',
    'purchases': r'nhap',
    'monthly': r'doanh thu|dthu',
    'rising': r'doanh thu|dthu',
    'best_month': r'doanh thu|dthu',
    'customer_debt': r'\bno\b|mua chiu',
    'stock_left': r'\bton\b|trong kho|\bcon\b',
    'gross_profit': r'(?:lai|loi|loi nhuan) gop|\b(?:loi|lai) nhat|\b(?:loi|lai) nhieu|cho loi|nhieu loi',
    'net_profit': r'(?:lai|loi|loi nhuan) rong',
    'stock_value': r'trong kho|ton kho|hang ton',
    'import_minus_sold': r'nhap',
    'staff_sales': r'nhan vien|\bnv\b|\bnv\d+\b',
    'top_supplier': SUPPLIER,
    'units_total': UNITS,
    'debt_age': r'\bno\b|cong no|\bthieu\b',
}
LISTED = {'monthly', 'rising', 'best_month'}
PAYMENT_EVIDENCE = {'cash': r'tien mat|\btm\b', 'transfer': r'chuyen khoan|\bck\b', 'cod': r'\bcod\b|thu ho|xi o di',
                    'debt': r'ghi no|mua chiu', 'ewallet': r'vi dien tu|\bvi\b|momo'}

CHANNEL_HINTS = {'store': r'tai (?:quay|tiem|quan|cho)|mang ve|truc tiep', 'tai tiem': r'tai (?:quay|tiem|quan|cho)|mang ve|truc tiep',
                 'phone': r'dien thoai|goi dien', 'zalo': r'zalo', 'san': r'\bsan\b|tmdt|thuong mai dien tu'}
# How a question asks for two channels' or payment ways' revenue at once, as the rules read it.
PAIR = r'lan luot|moi ben|tung ben|moi kenh|tung kenh|moi loai|\bcho (?:toi|minh)\b.*\bva\b|(?:bao nhieu|bnhiu|nhieu)\b.*\b(?:bao nhieu|bnhiu|nhieu)\b'

CHANNEL_EVIDENCE = r'tai quay|tai tiem|tai quan|tai cua hang|tai cho|mang ve|zalo|dien thoai|shopee|lazada|tiktok|tiki|\bsan\b|online|facebook|\bfb\b|\bweb|\bapp\b|grab|shopeefood|kenh'


def standard_months(plain):
    """Rewrite month forms such as t11/2025, th2-2026, thang 6/25 and 08/2025 as "thang M/YYYY"."""
    def month(match):
        number, year = int(match[1]), match[2]
        if not 1 <= number <= 12:
            return match[0]
        return f'thang {number}/{year if len(year) == 4 else "20" + year}'
    plain = re.sub(r'\b(?:thang|th|t) ?(\d{1,2}) ?(?:[/-]|nam) ?(\d{4}|\d{2})\b', month, plain)
    plain = re.sub(r'(?<![\w/-])(?<!thang )(?<!quy )(?<!quys )(\d{1,2})[/-](\d{4})\b', month, plain)
    # 24 to 27 cannot be a month, so 11/25 alone is never a day and month.
    return re.sub(r'(?<![\w/-])(?<!thang )(?<!quy )(?<!quys )(\d{1,2})[/-](2[4-7])(?![\w/-])', month, plain)


def check_evidence(slots, text):
    """Reject slots the question gives no sign of: a wrong answer costs a point, leaving the layer costs nothing."""
    from datamate.direct import spoken
    from datamate.normalized import name_key
    plain = spoken(standard_months(name_key(text, accents=True)))
    metric = slots.get('metric')
    filters = slots['filters'] = slots.get('filters') if isinstance(slots.get('filters'), dict) else {}
    # A list of one is the value itself.
    for key, value in list(filters.items()):
        if isinstance(value, list) and len(value) == 1:
            filters[key] = value[0]
    # It also files top-level settings under filters; the checks below decide whether they apply.
    for key in ('against', 'of', 'count', 'top'):
        if key in filters and key not in slots:
            slots[key] = filters.pop(key)
    # The model sometimes files count settings under filters; code reads the amount itself, and who is counted from the words.
    if metric in {'count_over', 'share'} and set(filters) - SLOTS:
        for key in set(filters) - SLOTS:
            filters.pop(key)
        if metric == 'share' or slots.get('count') not in COUNTED:
            slots['count'] = 'customers' if re.search(r'\bkhach\b', plain) else None
        metric = slots['metric'] = 'count_over'
    if metric == 'customer_debt' and 'customer_group' in filters and 'customer' not in filters:
        filters['customer'] = filters.pop('customer_group')
    if 'customer' in filters and metric != 'customer_debt':
        raise Unsupported('a named customer on ' + str(metric))
    pair = next((slot for slot in ('channel', 'payment') if isinstance(filters.get(slot), list)), None)
    if pair and (metric != 'revenue' or set(filters) != {pair} or not re.search(PAIR, plain)):
        raise Unsupported('two values not asked')
    # Stock left may be asked as whole big units and loose small ones.
    several = pair or metric in {'customer_debt', 'stock_left', 'debt_age'}
    # The model often fills plain revenue or orders for an average per order.
    if metric in {'revenue', 'orders'} and re.search(r'binh quan|bin quan|\bbq\b|trung bi', plain):
        if metric == 'orders' or not re.search(r'\b(?:don|dh|o do)\b', plain) or re.search(r'\bngay\b(?! gan)', plain):
            raise Unsupported('asks for an average')
        metric = slots['metric'] = 'average_order'
    if 'top' in slots and metric not in {'top_products', 'top_customers', 'top_debtors'}:
        raise Unsupported('top on ' + str(metric))
    # "Which one" asks for a name, not a one-item list.
    if slots.get('top') == 1:
        slots.pop('top')
    if re.search(r'\b(?:doanh thu|dthu)\b.*\bva (?:doanh thu|dthu)\b', plain) and not several:
        raise Unsupported('asks for several numbers')
    if metric == 'purchases' and (slots.get('filters') or re.search(r'binh quan|trung binh|\bgia\b', plain)):
        raise Unsupported('asks about one item or a price')
    product = filters.get('product')
    if isinstance(product, str):
        # The catalogue name is found from the question when the model shortens it.
        slots['asked'] = plain
    if metric not in {'purchases', 'buy_price', 'top_supplier', 'import_minus_sold'} and re.search(r'\bnhap\b', plain.replace(name_key(product, accents=True), ' ') if isinstance(product, str) else plain):
        raise Unsupported('asks about imports')
    if metric == 'shift_gap':
        slots['of'] = 'orders' if re.search(r'\b(?:don|dh)\b', plain) and not re.search(r'doanh thu|dthu|tien', plain) else 'revenue'
    if 'of' in slots and metric not in {'growth', 'shift_gap', 'best_day'}:
        slots.pop('of')
    if 'against' in slots and metric != 'growth':
        raise Unsupported('against on ' + str(metric))
    if len(re.findall(r'bao nhieu|bao nhiu|\bbn\b|bnhiu', plain)) > 1 and not several:
        raise Unsupported('asks for several numbers')
    if 'huy' in plain.split() and metric not in {'cancel_rate', 'cancelled'}:
        raise Unsupported('asks about cancelled orders')
    asked_period = {'best_day': r'\b(thang|tuan) nao\b', 'best_month': r'\b(ngay|tuan) nao\b'}.get(metric, r'\b(ngay|thang|tuan) nao\b')
    if re.search(asked_period, plain):
        raise Unsupported('asks which period')
    if re.search(r'hoat chat|ke don', plain):
        raise Unsupported('asks about drug attributes')
    # The rulebook ranks these by their own measure (days bought, orders containing the item...), not by money or quantity.
    if re.search(r'trung thanh|\bsop nhat|doi y|ua chuong|cham chi', plain):
        raise Unsupported('asks for a ranking the rulebook defines')
    # "Khach phai tra" is the order total after trade-ins and discounts, not revenue; a "phieu" average counts paid tickets only.
    if metric in {'revenue', 'average_order'} and re.search(r'phai tra|\bphieu\b', plain):
        raise Unsupported('asks for an amount other than revenue')
    if metric in {'cancelled', 'cancel_rate'} and re.search(r'\bvi\b|ly do|nguyen nhan', plain):
        raise Unsupported('asks for a cancel reason')
    if len({match for match in re.findall(CHANNEL_EVIDENCE, plain)}) > 1 and pair != 'channel':
        raise Unsupported('names several channels')
    # A threshold is what count_over asks about, and a run of months what the month lists ask about;
    # for every other metric they are conditions the layer cannot apply.
    outside = OUTSIDE
    if metric == 'count_over':
        outside = outside.replace('|tro len', '')
    if 'top' in slots or metric == 'top_debtors':
        outside = outside.replace('|liet ke', '')
    if metric in LISTED:
        outside = re.sub(r'lan luot\||moi thang\||tung thang\||lien tiep\||lien nhau\||lien tuc\||\|\\bluot\\b', '', outside)
    if pair:
        outside = re.sub(r'lan luot\||moi loai\||\|moi ben|\|\\bluot\\b', '', outside)
    if metric == 'price_effect':
        outside = re.sub(r'\|doi gia|\|tang gia|\|len gia|\|ke tu|\|\(\?:hon\|qua\) \\d\+ ngay|\|\\blen \\d', '', outside)
    if metric == 'gross_profit':
        outside = outside.replace('|lai gop', '').replace('|loi nhat', '')
    if metric == 'net_profit':
        outside = outside.replace('|lai rong', '')
    if metric == 'stock_value':
        outside = outside.replace('|gia von', '')
    # Each of these is the condition its own metric reads.
    if metric == 'staff_sales':
        outside = outside.replace('|nhan vien', '')
    if metric == 'top_supplier':
        outside = outside.replace('|nha cung cap|\\bncc\\b', '')
    if metric == 'debt_age':
        outside = re.sub(r'lan luot\||\|tuoi no|\|\(\?:hon\|qua\) \\d\+ ngay|\|tren \\d|\|\\bluot\\b', '', outside)
    # Words of the product's own name (bay tho ho) are not conditions.
    if re.search(outside, plain.replace(name_key(product, accents=True), ' ') if isinstance(product, str) else plain):
        raise Unsupported('asks for something outside the layer')
    if re.search(UNITS, plain) and metric not in {'top_products', 'units', 'price', 'buy_price', 'price_effect', 'stock_left', 'units_total', 'import_minus_sold'}:
        raise Unsupported('asks for units sold')
    for name, pattern in METRIC_EVIDENCE.items():
        if metric == name and not re.search(pattern, plain):
            raise Unsupported(name + ' not in question')
    if metric == 'revenue' and re.search(COUNTED['orders'], plain):
        raise Unsupported('asks for a count of orders')
    if metric in {'revenue', 'average_order'} and re.search(r'\bgia\b(?! tri)', plain.replace(name_key(product, accents=True), ' ') if isinstance(product, str) else plain):
        raise Unsupported('asks about a price')
    widened(slots, plain)
    if metric == 'debtors' and 'tong' in plain.split():
        raise Unsupported('asks for a debt total')
    if metric == 'top_customers':
        # "it nhat" (at least) and "gan nhat" (latest) are not superlatives; counting customers is count_over.
        best = re.sub(r'\b(?:it|gan|moi) nhat\b', ' ', plain)
        if (not re.search(r'\b(?:khach|kh)\b|\bai\b', plain) or not re.search(r'nhat|nhut', best)
                or re.search(r'\bno\b|cong no|%|phan tram|ti le|ty le|\bdem\b|' + COUNTED['customers'], plain)):
            raise Unsupported('top customers not asked')
        count = re.search(r'(?<![/\d])\b(\d+) (?:khach|kh|nguoi)\b|\btop (\d+)', plain)
        if count:
            slots['top'] = int(count[1] or count[2])
        else:
            slots.pop('top', None)
        if re.search(r'nhi?e?u (don|lan)|so don', plain):
            slots['of'] = 'orders'
    period = slots.get('period')
    if isinstance(period, dict) and 'shift' in period:
        filters['shift'] = period.pop('shift')
    morning, afternoon = re.search(r'ca sang', plain), re.search(r'ca chieu', plain)
    if metric != 'shift_gap' and bool(morning) != bool(afternoon):
        filters['shift'] = 'morning' if morning else 'afternoon'
    if filters.get('shift') and not (morning or afternoon):
        raise Unsupported('shift not in question')
    # A question mark or comma glued to a word must not hide it.
    words = {word.strip('?!.,:;') for word in plain.split()}
    for slot in ('product_group', 'customer_group', 'product', 'customer'):
        if filters.get(slot) and not all(word.strip('?!.,:;') in words for word in name_key(filters[slot], accents=True).split()):
            raise Unsupported(slot + ' not in question')
    if filters.get('product_group') and not re.search(r'nhom|loai|danh muc', plain):
        raise Unsupported('product_group not named as a group')
    # Accents are gone by now, so le (retail) also matches the street name Le Trong Tan.
    customer_group = filters.get('customer_group')
    if customer_group and not re.search(r'khach (?:hang )?(?:thuoc )?(?:nhom )?' + re.escape(name_key(customer_group, accents=True)), plain):
        raise Unsupported('customer_group not named as customers')
    if pair:
        # Each of the two needs its own words, and the answer follows the order they are asked in.
        places = []
        for value in filters[pair]:
            key = name_key(str(value), accents=True)
            hint = PAYMENT_EVIDENCE.get(value) if pair == 'payment' else next((hint for label, hint in CHANNEL_HINTS.items() if label in key), None)
            found = re.search(hint, plain) if hint else None
            if not found:
                raise Unsupported(pair + ' not in question')
            places.append(found.start())
        if places[0] == places[1]:
            raise Unsupported('two values not told apart')
        filters[pair] = [value for _, value in sorted(zip(places, filters[pair]))]
    channel = None if pair == 'channel' else filters.get('channel')
    if channel:
        # Each known channel needs its own words; "tai cua hang" alone means the whole shop, not the counter.
        key = name_key(str(channel), accents=True)
        if not re.search(next((hint for label, hint in CHANNEL_HINTS.items() if label in key), CHANNEL_EVIDENCE), plain):
            raise Unsupported('channel not in question')
    # A named group the model left out of the filters would answer for the whole shop.
    if re.search(r'\bnhom (?!khach\b|tuoi no\b)', plain) and not (filters.get('product_group') or filters.get('customer_group')):
        raise Unsupported('group named but not filtered')
    payment = None if pair == 'payment' else filters.get('payment')
    if payment and not re.search(PAYMENT_EVIDENCE.get(payment, '$^'), plain):
        raise Unsupported('payment not in question')
    fix_period(slots, plain)


HOW_MANY = r'(?:may|bao nhieu|bao nhiu|\bbn\b|bnhiu)'
ITEM = r'(?:mat hang|san pham|sp|mon|hang)'
COUNTED = {'days': HOW_MANY + r' (?:ngay|bua)\b|\bdem\b.*\b(?:ngay|bua)\b|\bso ngay\b',
           'orders': HOW_MANY + r' (?:don|dh|bill|hoa don)\b|\bso (?:don|dh)\b',
           'customers': HOW_MANY + r' (?:khach|nguoi)\b|nhieu khach|\bso khach\b|\bdem\b.*\bkhach\b',
           'items': HOW_MANY + ' ' + ITEM + r'\b|\bso ' + ITEM + r'\b'}


def widened(slots, plain):
    """Evidence for the metrics the rules also answer; code reads their amounts and units, as with dates."""
    metric = slots.get('metric')
    filters = slots.get('filters') or {}
    if metric == 'top_products':
        if re.search(r'\bdong may\b', plain):
            raise Unsupported('asks for phone model line ranking')
        # Ranked by money only when the question says so; quantity is the rulebook default.
        if re.search(r'nhi?e?u tien|doanh thu|dthu', plain):
            slots['of'] = 'revenue'
        if re.search(r'it nhat|thap nhat|cham nhat', plain):
            raise Unsupported('asks for the lowest')
    if metric == 'net_profit':
        if filters or re.search(r'%|phan tram|ti le|ty le|chiem|\bnhom\b|nhan vien|\bnao\b', plain):
            raise Unsupported('net profit with extra conditions')
        return
    if metric == 'stock_value':
        if filters or not re.search(r'gia von|dang (?:bao nhieu|gia)', plain):
            raise Unsupported('stock value not asked')
        slots['asked'] = plain
        days = set(re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain))
        if len(days) > 1:
            raise Unsupported('multiple stock days')
        if days:
            (d, m, y), = days
            slots['period'] = {'kind': 'day', 'date': f'{y}-{int(m):02}-{int(d):02}'}
        elif re.search(r'hom nay|hnay|bua nay|hien tai', plain):
            slots['period'] = {'kind': 'today'}
        else:
            raise Unsupported('stock day not read')
        return
    if metric == 'import_minus_sold':
        if set(filters) != {'product'} or not re.search(r'\btru\b|chenh', plain) or re.search(r'%|phan tram|\bgia\b|\btien\b', plain):
            raise Unsupported('import minus sold not asked')
        slots['asked'] = plain
        return
    if metric == 'customer_debt':
        if set(filters) != {'customer'} or not re.search(r'(?:no|ghi no|mua chiu) them', plain) or not re.search(r'\btra\b', plain):
            raise Unsupported('new and repaid debt not asked')
        return
    if metric in {'staff_sales', 'top_supplier', 'units_total', 'debt_age'}:
        if filters:
            raise Unsupported('filters on ' + metric)
        # Code reads the staff member, the unit and the age bands from the words, as the rules do.
        slots['asked'] = plain
        lowest = re.search(r'\bit (?:tien |don )?nhat|thap nhat|kem nhat|cham nhat', plain)
        if metric == 'staff_sales':
            slots['item'] = bool(re.search(r'\bnao\b|\bai\b', plain) and re.search(r'nhat|nhut', plain))
            if (lowest or re.search(r'%|phan tram|ti le|ty le|chiem|\b(?:don|dh)\b', plain)
                    or not re.search(r'tien|doanh thu|gioi|\bban (?:duoc|ra)\b', plain)):
                raise Unsupported('staff sales not asked')
        elif metric == 'top_supplier':
            slots['main'] = bool(re.search(r'(?:' + SUPPLIER + r') chinh', plain))
            if lowest or not re.search(r'\bnao\b|\bai\b', plain) or not (slots['main'] or re.search(r'(?:nhieu|cao|lon) ?(?:tien )?nh[au]t|nhieu tien', plain)):
                raise Unsupported('top supplier not asked')
        elif metric == 'units_total':
            if re.search(r'%|phan tram|ti le|ty le|chiem', plain):
                raise Unsupported('asks for a percent of units')
        else:
            days = set(re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain))
            if len(days) == 1:
                (d, m, y), = days
                slots['period'] = {'kind': 'until', 'date': f'{y}-{int(m):02}-{int(d):02}'}
            elif not days and re.search(r'hom nay|hnay|bua nay', plain):
                slots['period'] = {'kind': 'until_today'}
            else:
                raise Unsupported('debt day not read')
        return
    if metric in {'stock_left', 'gross_profit'}:
        if re.search(r'%|phan tram|ti le|ty le|chiem|\bnhom\b|nhan vien', plain):
            raise Unsupported(metric + ' with a percent, group or staff')
        if metric == 'gross_profit':
            # Net profit also takes the expense book off; which item ranks by gross profit as the rules do.
            item = re.search(r'\b(?:hang|mat hang|san pham|sp|mon) nao\b|\b(?:san pham|mat hang|sp|mon)\b.*\bla gi\b', plain)
            if filters or re.search(r'\brong\b|chi phi', plain) or (item and not re.search(r'nhat|nhut', plain)) or not (item or re.search(r'\bgop\b', plain)):
                raise Unsupported('gross profit not asked')
            slots['item'] = bool(item)
            return
        from datamate.normalized import name_key
        bare = plain.replace(name_key(filters.get('product') or '', accents=True), ' ')
        days = set(re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain))
        # Sales, imports, lots and money are other questions about the same stock.
        if set(filters) != {'product'} or len(days) > 1 or re.search(r'\bban\b|\bnhap\b|han dung|het han|(?<!may )(?<!nhieu )(?<!bnhiu )(?<!bn )\blo\b|gia von|\btien\b', bare):
            raise Unsupported('stock left not asked')
        if days:
            (d, m, y), = days
            slots['period'] = {'kind': 'day', 'date': f'{y}-{int(m):02}-{int(d):02}'}
        elif re.search(r'hom nay|hnay|bua nay', plain):
            slots['period'] = {'kind': 'today'}
        else:
            raise Unsupported('stock day not read')
        return
    if metric not in {'units', 'best_day', 'count_over', 'unsold', 'price', 'buy_price', 'top_debtors', 'price_effect'} | LISTED:
        return
    if re.search(r'%|phan tram|ti le|ty le|chiem', plain):
        if metric == 'count_over' and slots.get('count') == 'customers':
            slots['percent'] = True
        elif metric != 'price_effect':
            raise Unsupported('asks for a percent of ' + metric)
    from datamate.normalized import name_key
    bare = plain
    for value in filters.values():
        bare = bare.replace(name_key(value, accents=True), ' ')
    days = list(dict.fromkeys(re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain)))
    if metric == 'price_effect':
        days = set(re.findall(r'(\d+) ngay (?:ke tu|ngay truoc|ngay trc|truoc|trc|dau|tu|sau|lien truoc|lien trc)', plain))
        dates = list(dict.fromkeys(re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain)))
        if (set(filters) != {'product'} or len(days) != 1 or len(dates) != 1 or not re.search(r'tang gia|len gia|doi gia|\blen \d', plain)
                or not re.search(r'%|phan tram', plain)):
            raise Unsupported('price change effect not read')
        (d, m, y), = dates
        slots.update(days=int(next(iter(days))), period={'kind': 'day', 'date': f'{y}-{int(m):02}-{int(d):02}'})
        return
    if metric in {'price', 'buy_price'}:
        if set(filters) != {'product'}:
            raise Unsupported(metric + ' needs one product')
        if metric == 'price':
            if len(days) != 1 or not re.search(r'\bgia\b', bare) or re.search(r'\bnhap\b|\bvon\b|binh quan|trung binh', bare):
                raise Unsupported('price on a day not asked')
            (d, m, y), = days
            slots['period'] = {'kind': 'day', 'date': f'{y}-{int(m):02}-{int(d):02}'}
            return
        unit = re.search(r'\b(?:mot|1) ([\w.]+(?: \d+\w*)?)\b', bare)
        if not re.search(r'\bnhap\b', bare) or not re.search(r'binh quan|trung binh', bare) or unit is None:
            raise Unsupported('average import price not asked')
        slots['unit'] = unit[1]
        return
    if metric == 'units':
        # Stock left and imports are counted in units too, but they are not sales.
        if re.search(r'nhap|\bton\b|\bkho\b|cuoi ngay|\bcon\b|\bgia\b', bare) or not re.search(r'\bban\b|\bso ', bare):
            raise Unsupported('units not sold')
        if set(filters) - {'product', 'product_group'} or len(filters) != 1:
            raise Unsupported('units need one product or group')
        unit = re.search(r'(?:' + HOW_MANY + r'|\bso) (\w+)', plain)
        if unit is None:
            raise Unsupported('unit not named')
        slots['unit'] = unit[1]
        return
    if filters:
        raise Unsupported('filters on ' + metric)
    if metric in LISTED:
        rising = re.search(r'tang lien tuc|(?:luon|deu) (?:tang|cao hon)|cao hon.*cao hon', plain)
        # Outside a rise month after month, "hon" compares months in some other way.
        if re.search(r'trung binh|binh quan', plain) or (not rising and re.search(r'\bhon\b', plain)):
            raise Unsupported('asks for something else about the months')
        best = re.search(r'\bthang nao\b', plain) and re.search(r'nhat|nhut', plain) and not re.search(r'thap nhat|it nhat', plain)
        listed = re.search(r'moi thang|tung thang|lan luot|thu tu|theo thoi gian|\d so\b|tu thang cu', plain)
        if metric != ('rising' if rising else 'best_month' if best else 'monthly' if listed else None):
            raise Unsupported(metric + ' not asked')
        # Code reads the months, as it does dates; compute turns them into periods once it knows today.
        slots['listed'] = plain
    elif metric == 'top_debtors':
        count = re.search(r'(?:top )?(\d+) (?:khach|kh)\b', plain) or re.search(r'\btop (\d+)', plain)
        if not count or not re.search(r'\bno\b|cong no', plain) or not re.search(r'nhi?e?u|lon nhat|nhat', plain):
            raise Unsupported('top debtors not asked')
        slots['top'] = int(count[1])
        if len(days) == 1:
            (d, m, y), = days
            slots['period'] = {'kind': 'until', 'date': f'{y}-{int(m):02}-{int(d):02}'}
        elif not days and re.search(r'hom nay|hnay|bua nay', plain):
            slots['period'] = {'kind': 'until_today'}
        else:
            raise Unsupported('debt day not read')
    elif metric == 'best_day':
        if not re.search(r'\bngay ?nao\b', plain) or re.search(r'thap nhat|it nhat|cham nhat', plain):
            raise Unsupported('best day not asked')
        money_asked = re.search(r'doanh thu|dthu|nhi?e?u tien|ban dat', plain)
        if not re.search(r'nhat|nhut', plain) or not (money_asked or re.search(r'\bdon\b|\bdh\b', plain)):
            raise Unsupported('best day not asked')
        slots['of'] = 'revenue' if money_asked else 'orders'
    elif metric == 'count_over':
        from datamate.direct import at_least
        count = slots.get('count')
        if count not in COUNTED or not re.search(r'\bkhach\b' if slots.get('percent') else COUNTED[count], plain):
            raise Unsupported('count of ' + repr(count) + ' not in question')
        if count in {'days', 'orders'}:
            slots['min'] = at_least(plain)
            if not re.search(r'doanh thu|dthu|nhi?e?u tien|tien hang|gia tri|ban duoc|ban ra', plain):
                raise Unsupported('revenue not in question')
        else:
            found = re.search(r'(\d+) (?:don|dh)\b', re.sub(r'\bthang \d+(?:/\d+)?(?: nam \d+)?', ' ', plain))
            slots['min'] = int(found[1]) if found and re.search(r'tro len|it nhat', plain) else None
        if not slots['min']:
            raise Unsupported('amount not read')
    elif metric == 'unsold':
        if not re.search(ITEM + r' (?:bi )?e\b|\bbi e\b|\b(?:khong|ko|k) ban (?:duoc|dc|ra)|hok ban|khong ai mua', plain):
            raise Unsupported('unsold not asked')
        if not re.search(COUNTED['items'] + r'|\bdem\b', plain):
            raise Unsupported('count of items not asked')


def fix_period(slots, plain):
    """Plain date phrases are read by code, since the model miscounts them; a conflict leaves the layer."""
    metric = slots.get('metric')
    # These read their own dates in widened.
    if metric in LISTED | {'price', 'top_debtors', 'price_effect', 'stock_left', 'debt_age', 'stock_value'}:
        return
    months = {(int(m), int(y)) for m, y in re.findall(r'\b(?:thang|th) (\d{1,2})(?:/| nam )(\d{4})', plain)}
    if metric == 'growth':
        if len(months) == 2:
            (m1, y1), (m2, y2) = sorted(months, key=lambda pair: pair[1])
            if m1 != m2 or y2 - y1 != 1:
                raise Unsupported('compares unusual months')
            slots.update(period={'kind': 'month', 'month': m2, 'year': y2}, against='last_year')
            return
        if re.search(r'cung ky nam|nam ngoai|nam truoc|nam trc|nam roi', plain):
            if len(months) != 1:
                raise Unsupported('same period last year needs a named month')
            slots['against'] = 'last_year'
        else:
            slots.pop('against', None)
    elif len(months) > 1:
        raise Unsupported('names several months')
    if metric in {'debt_total', 'debtors'}:
        return
    quarter = re.search(r'\b(?:quy?s?|q) ?(\d)(?:/| nam )(\d{4})', plain)
    # A month named without its year is the latest one (rulebook section 2); the model wrote "last month" for "thang 6".
    bare = {int(m) for m in re.findall(r'\b(?:thang|th) (\d{1,2})\b(?!/| nam\b)', plain) if 1 <= int(m) <= 12}
    if not months and bare and (len(bare) > 1 or re.search(r'\bngay \d{1,2} (?:thang|th) \d', plain)):
        raise Unsupported('months without a year')
    if months:
        (month, year), = months
        slots['period'] = {'kind': 'month', 'month': month, 'year': year}
    elif bare:
        slots['period'] = {'kind': 'month', 'month': bare.pop()}
    elif quarter:
        slots['period'] = {'kind': 'quarter', 'quarter': int(quarter[1]), 'year': int(quarter[2])}
    elif len(set(re.findall(r'\b\d{1,2}/\d{1,2}/\d{4}\b', plain))) == 2 and re.search(r'\btu\b', plain):
        (d1, m1, y1), (d2, m2, y2) = re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain)[:2]
        slots['period'] = {'kind': 'range', 'start': f'{y1}-{int(m1):02}-{int(d1):02}', 'end': f'{y2}-{int(m2):02}-{int(d2):02}'}
    elif re.search(r'(\d+) ngay (?:gan nhat|gan day|qua|vua qua)', plain):
        slots['period'] = {'kind': 'last_days', 'days': int(re.search(r'(\d+) ngay (?:gan nhat|gan day|qua|vua qua)', plain)[1])}
    elif re.search(r'\bdau nam|tu (?:ngay )?0?1/0?1\b|nam (?:nay|ni).*(?:hom nay|hnay|bua nay)', plain):
        slots['period'] = {'kind': 'year_to_date'}
    elif re.search(r'(tu dau|dau) (thang|th) (?:nay|ni)|\b(?:thang|th) (?:nay|ni)\b', plain):
        slots['period'] = {'kind': 'this_month'}
    elif re.search(r'tuan (roi|truoc|trc)', plain):
        slots['period'] = {'kind': 'last_week'}
    elif re.search(r'tuan nay', plain):
        slots['period'] = {'kind': 'this_week'}
    elif re.search(r'hom qua|hqua|bua qua', plain):
        slots['period'] = {'kind': 'yesterday'}
    elif re.search(r'(?:den|toi) het', plain) and re.search(r'hom nay|hnay|bua nay', plain):
        raise Unsupported('range ending today')
    elif re.search(r'hom nay|hnay|bua nay', plain):
        slots['period'] = {'kind': 'today'}
    if slots.get('against') == 'last_year' and slots['period'].get('kind') != 'month':
        raise Unsupported('same period last year needs a named month')


def sale_filters(filters):
    """Rulebook filters over `sales s JOIN orders o`, as SQL conditions and parameters."""
    clauses, params = [], []
    filters = filters or {}
    if filters.get('shift'):
        low, high = SHIFTS[filters['shift']]
        clauses.append('CAST(substr(o.created_at, 12, 2) AS INTEGER) BETWEEN ? AND ?')
        params += [low, high]
    for slot, column in [('channel', 's.channel'), ('payment', 's.payment_method'), ('product_group', 's.product_group'),
                         ('product', 's.product_name'), ('customer_group', 's.customer_group')]:
        if filters.get(slot):
            clauses.append(column + ' = ?')
            params.append(filters[slot])
    return clauses, params


class Metrics:
    def __init__(self, data):
        self.data = data
        self.capabilities = set(data.report['source_capabilities'])
        self.first, self.last = self.rows('SELECT MIN(order_date), MAX(order_date) FROM orders')[0]

    def rows(self, sql, params=()):
        return self.data.query(sql, list(params), timeout=10, max_rows=1000000)['rows']

    def labels(self):
        """The filter values this shop records, shown to the model and used to reject invented ones."""
        found = {}
        for slot, sql in [('channel', 'SELECT DISTINCT channel FROM orders'), ('payment', 'SELECT DISTINCT payment_method FROM orders'),
                          ('product_group', 'SELECT DISTINCT product_group FROM products'),
                          ('customer_group', 'SELECT DISTINCT customer_group FROM customers')]:
            found[slot] = sorted(row[0] for row in self.rows(sql) if row[0] is not None)
        return found

    def check_filters(self, filters, asked=None):
        from datamate.normalized import name_key
        labels = self.labels()
        for slot, value in (filters or {}).items():
            if slot not in SLOTS:
                raise Unsupported('filter ' + slot)
            if slot == 'shift':
                if value not in SHIFTS:
                    raise Unsupported('shift ' + repr(value))
            elif slot == 'product':
                if not self.rows('SELECT 1 FROM products WHERE product_name = ?', [value]):
                    # The model often drops accents or case from a name it copies; one catalogue match is the same product.
                    names = [row[0] for row in self.rows('SELECT product_name FROM products')]
                    same = {name for name in names if name_key(name, accents=True) == name_key(value, accents=True)}
                    if len(same) != 1 and asked:
                        # It also shortens names (Ma heo nuong became heo nuong): the longest one catalogue name standing whole
                        # in the question and holding every word the model wrote is meant.
                        text, mine = ' ' + ' '.join(re.findall(r'\w+', asked)) + ' ', set(re.findall(r'\w+', name_key(value, accents=True)))
                        whole = {name: re.findall(r'\w+', name_key(name, accents=True)) for name in names}
                        found = [name for name, words in whole.items() if words and ' ' + ' '.join(words) + ' ' in text and mine <= set(words)]
                        longest = max((len(whole[name]) for name in found), default=0)
                        same = {name for name in found if len(whole[name]) == longest}
                    if len(same) != 1:
                        aliases = self.rows('SELECT p.product_name, a.alias FROM product_aliases a JOIN products p USING(product_id)')
                        same = {name for name, alias in aliases if name_key(alias or '', accents=True) == name_key(value, accents=True)}
                        if len(same) != 1 and asked:
                            text = ' ' + ' '.join(re.findall(r'\w+', asked)) + ' '
                            mine = set(re.findall(r'\w+', name_key(value, accents=True)))
                            found = [(name, re.findall(r'\w+', name_key(alias or '', accents=True))) for name, alias in aliases]
                            found = [(name, words) for name, words in found if words and ' ' + ' '.join(words) + ' ' in text and mine <= set(words)]
                            longest = max((len(words) for _, words in found), default=0)
                            same = {name for name, words in found if len(words) == longest}
                    if len(same) != 1:
                        raise Unsupported('product ' + repr(value))
                    filters['product'], = same
            elif slot == 'customer':
                self.customer_id(value)
            elif isinstance(value, list):
                if len(value) != 2 or len(set(value)) != 2 or not set(value) <= set(labels[slot]):
                    raise Unsupported(slot + ' ' + repr(value))
            elif value not in labels[slot]:
                raise Unsupported(slot + ' ' + repr(value))

    def customer_id(self, name):
        from datamate.normalized import name_key
        found = {row[0] for row in self.rows('SELECT customer_id, customer_name FROM customers')
                 if name_key(row[1] or '', accents=True) == name_key(str(name), accents=True)}
        if len(found) != 1:
            raise Unsupported('customer ' + repr(name))
        return next(iter(found))

    def need(self, capability):
        if capability not in self.capabilities:
            raise Unsupported('shop has no ' + capability)

    def sales(self, select, start, end, filters, tail=''):
        clauses, params = sale_filters(filters)
        where = ' AND '.join(['s.order_date BETWEEN ? AND ?'] + clauses)
        return self.rows(f'SELECT {select} FROM sales s JOIN orders o USING(order_id) WHERE {where} {tail}',
                         [start.isoformat(), end.isoformat()] + params)

    def revenue(self, start, end, filters):
        return self.sales('SUM(s.line_revenue)', start, end, filters)[0][0] or 0

    def order_count(self, start, end, filters):
        return self.sales('COUNT(DISTINCT s.order_id)', start, end, filters)[0][0]

    def base(self, metric, start, end, filters):
        return {'revenue': self.revenue, 'orders': self.order_count}[metric](start, end, filters)

    def compute(self, slots, as_of):
        metric = slots.get('metric')
        handler = getattr(self, 'metric_' + str(metric), None)
        if handler is None:
            raise Unsupported('metric ' + repr(metric))
        self.check_filters(slots.get('filters'), slots.get('asked'))
        if 'listed' in slots:
            slots['months'] = listed_months(slots['listed'], as_of)
            if not slots['months']:
                raise Unsupported('months not read')
            (m1, y1), (m2, y2) = min(slots['months'], key=lambda m: (m[1], m[0])), max(slots['months'], key=lambda m: (m[1], m[0]))
            slots['period'] = {'kind': 'range', 'start': month_range(y1, m1)[0].isoformat(), 'end': month_range(y2, m2)[1].isoformat()}
        start, end = resolve_period(slots['period'], as_of)
        # A period wholly outside the shop's dates has no data, which differs from zero (section 7).
        if end.isoformat() < self.first or start.isoformat() > self.last:
            return {'type': 'unanswerable', 'reason': 'period is outside the data'}
        return handler(slots, start, end, as_of)

    def metric_revenue(self, slots, start, end, as_of):
        filters = slots.get('filters') or {}
        pair = next((slot for slot, value in filters.items() if isinstance(value, list)), None)
        if pair:
            # Two channels or payment ways asked at once are answered in the order asked.
            return {'type': 'list', 'answer': [money(self.revenue(start, end, {**filters, pair: value})) for value in filters[pair]]}
        return {'type': 'number', 'answer': money(self.revenue(start, end, filters))}

    def metric_orders(self, slots, start, end, as_of):
        return {'type': 'number', 'answer': self.order_count(start, end, slots.get('filters'))}

    def metric_average_order(self, slots, start, end, as_of):
        count = self.order_count(start, end, slots.get('filters'))
        if not count:
            raise Unsupported('no orders to average')
        return {'type': 'number', 'answer': money(self.revenue(start, end, slots.get('filters')) / count)}

    def metric_cancel_rate(self, slots, start, end, as_of):
        total, cancelled = self.rows("SELECT COUNT(*), SUM(status = 'cancelled') FROM orders WHERE order_date BETWEEN ? AND ?",
                                     [start.isoformat(), end.isoformat()])[0]
        if not total:
            raise Unsupported('no orders in period')
        return {'type': 'percent', 'answer': two(100 * cancelled / total)}

    def metric_cancelled(self, slots, start, end, as_of):
        count = self.rows("SELECT COUNT(*) FROM orders WHERE status = 'cancelled' AND order_date BETWEEN ? AND ?",
                          [start.isoformat(), end.isoformat()])[0][0]
        return {'type': 'number', 'answer': count}

    def metric_growth(self, slots, start, end, as_of):
        filters, base = slots.get('filters'), slots.get('of', 'revenue')
        p_start, p_end = previous_period(slots['period'], start, end, slots.get('against'))
        if p_end.isoformat() < self.first or p_start.isoformat() > self.last:
            return {'type': 'unanswerable', 'reason': 'comparison period is outside the data'}
        before = self.base(base, p_start, p_end, filters)
        if not before:
            raise Unsupported('nothing to compare with')
        return {'type': 'percent', 'answer': two(100 * (self.base(base, start, end, filters) - before) / before)}

    def metric_shift_gap(self, slots, start, end, as_of):
        base = slots.get('of', 'revenue')
        morning = self.base(base, start, end, {'shift': 'morning'})
        if not morning:
            raise Unsupported('no morning shift to compare with')
        return {'type': 'percent', 'answer': two(100 * (self.base(base, start, end, {'shift': 'afternoon'}) - morning) / morning)}

    def metric_share(self, slots, start, end, as_of):
        whole = self.revenue(start, end, None)
        if not whole:
            raise Unsupported('no revenue in period')
        return {'type': 'percent', 'answer': two(100 * self.revenue(start, end, slots['filters']) / whole)}

    def metric_top_products(self, slots, start, end, as_of):
        self.need('line_items')
        filters = dict(slots.get('filters') or {})
        measure = 'SUM(s.line_revenue)' if slots.get('of') == 'revenue' else 'SUM(s.basic_quantity)'
        rows = self.sales('s.product_name, ' + measure, start, end, filters,
                          'AND NOT COALESCE(s.is_service, 0) GROUP BY s.product_id ORDER BY 2 DESC, s.product_id')
        return self.ranked(slots, rows)

    def metric_top_customers(self, slots, start, end, as_of):
        if self.not_kept('customer_identity'):
            return self.not_kept('customer_identity')
        measure = 'COUNT(DISTINCT s.order_id)' if slots.get('of') == 'orders' else 'SUM(s.line_revenue)'
        rows = self.sales('s.customer_name, ' + measure, start, end, slots.get('filters'),
                          'AND s.customer_id IS NOT NULL GROUP BY s.customer_id ORDER BY 2 DESC, s.customer_id')
        return self.ranked(slots, rows)

    def ranked(self, slots, rows):
        if not rows:
            raise Unsupported('nothing to rank')
        # A tie at the cut makes the answer depend on the code order, which the question cannot have meant.
        size = slots.get('top', 1)
        if len(rows) > size and rows[size - 1][1] == rows[size][1]:
            raise Unsupported('tie at the cut')
        if 'top' in slots:
            return {'type': 'list', 'answer': [row[0] for row in rows[:slots['top']]]}
        return {'type': 'string', 'answer': rows[0][0]}

    # The metrics below share the code rules' definitions; records after today have not happened yet.

    def plain_span(self, slots, start, end, as_of):
        if slots.get('filters'):
            raise Unsupported('filters on ' + slots['metric'])
        return start.isoformat(), min(end, date.fromisoformat(as_of)).isoformat()

    def metric_units(self, slots, start, end, as_of):
        self.need('line_items')
        from datamate.normalized import name_key
        filters = slots['filters']
        column, value = next(iter(filters.items()))
        column = {'product': 'product_name', 'product_group': 'product_group'}[column]
        # The question must count in the unit the catalogue sells in, or the sum is in the wrong unit.
        units = {row[0] for row in self.rows(f'SELECT DISTINCT COALESCE(p.base_unit, l.sale_unit) FROM order_lines l JOIN products p USING(product_id) WHERE p.{column} = ?', [value])}
        if len(units) != 1 or None in units or name_key(next(iter(units)), accents=True) != slots.get('unit'):
            raise Unsupported('unit differs from the catalogue')
        sold = self.sales('SUM(s.basic_quantity)', start, min(end, date.fromisoformat(as_of)), filters)[0][0]
        if not sold:
            raise Unsupported('nothing sold')
        return {'type': 'number', 'answer': int(sold) if float(sold).is_integer() else two(sold)}

    def metric_best_day(self, slots, start, end, as_of):
        span = self.plain_span(slots, start, end, as_of)
        column = 'order_count' if slots.get('of') == 'orders' else 'revenue'
        rows = self.rows(f'SELECT order_date, {column} FROM daily_revenue WHERE order_date BETWEEN ? AND ? ORDER BY 2 DESC, order_date', span)
        if not rows or (len(rows) > 1 and rows[0][1] == rows[1][1]):
            raise Unsupported('no single best day')
        return {'type': 'date', 'answer': rows[0][0]}

    def not_kept(self, capability):
        """A shop that does not record what the question needs cannot answer it, which differs from zero (rulebook section 7)."""
        if capability not in self.capabilities:
            return {'type': 'unanswerable', 'reason': 'shop does not record ' + capability.replace('_', ' ')}
        return None

    def metric_count_over(self, slots, start, end, as_of):
        span = self.plain_span(slots, start, end, as_of)
        if slots['count'] == 'customers' and self.not_kept('customer_identity'):
            return self.not_kept('customer_identity')
        sql = {'days': 'SELECT COUNT(*) FROM daily_revenue WHERE order_date BETWEEN ? AND ? AND revenue >= ?',
               'orders': 'SELECT COUNT(*) FROM (SELECT order_id FROM sales WHERE order_date BETWEEN ? AND ? GROUP BY order_id HAVING SUM(line_revenue) >= ?)',
               'customers': 'SELECT COUNT(*) FROM (SELECT customer_id FROM sales WHERE customer_id IS NOT NULL AND order_date BETWEEN ? AND ? '
                            'GROUP BY customer_id HAVING COUNT(DISTINCT order_id) >= ?)',
               'items': 'SELECT COUNT(*) FROM (SELECT product_id FROM sales WHERE NOT COALESCE(is_service, 0) AND order_date BETWEEN ? AND ? '
                        'GROUP BY product_id HAVING COUNT(DISTINCT order_id) >= ?)'}[slots['count']]
        if slots.get('percent'):
            # Share of the customers who bought in the period, as the rules compute it.
            counts = self.rows('SELECT COUNT(DISTINCT order_id) FROM sales WHERE customer_id IS NOT NULL AND order_date BETWEEN ? AND ? GROUP BY customer_id', list(span))
            if not counts:
                raise Unsupported('no customers bought')
            return {'type': 'percent', 'answer': two(100 * sum(count >= slots['min'] for count, in counts) / len(counts))}
        return {'type': 'number', 'answer': self.rows(sql, [*span, slots['min']])[0][0]}

    def metric_unsold(self, slots, start, end, as_of):
        """Items sold before the period with no sale inside it (rulebook section 5)."""
        span = self.plain_span(slots, start, end, as_of)
        rows = self.rows('SELECT DISTINCT product_name FROM sales WHERE order_date < ? AND product_id NOT IN '
                         '(SELECT product_id FROM sales WHERE order_date BETWEEN ? AND ?)', [span[0], *span])
        return {'type': 'number', 'answer': len(rows)}

    def metric_purchases(self, slots, start, end, as_of):
        self.need('purchase_prices')
        rows = self.rows("""SELECT source_status, SUM(COALESCE(recorded_total, quantity * unit_price, 0)) FROM purchase_lines
            WHERE import_date BETWEEN ? AND ? GROUP BY source_status""", [start.isoformat(), end.isoformat()])
        from datamate.normalized import name_key
        return {'type': 'number', 'answer': money(sum(row[1] for row in rows if 'huy' not in name_key(row[0] or '', accents=True)))}

    def metric_debt_collected(self, slots, start, end, as_of):
        self.need('payments')
        self.need('payment_method')
        total = self.rows("""SELECT SUM(p.amount) FROM payments p JOIN orders o USING(order_id)
            WHERE o.payment_method = 'debt' AND o.status != 'cancelled' AND p.payment_date > o.order_date
            AND p.payment_date BETWEEN ? AND ?""", [start.isoformat(), end.isoformat()])[0][0]
        return {'type': 'number', 'answer': money(total or 0)}

    def metric_customer_debt(self, slots, start, end, as_of):
        """A named customer's new debt and later repayments in the period (rulebook section 4), as the rules compute them."""
        self.need('payments')
        person, window = self.customer_id(slots['filters']['customer']), [start.isoformat(), end.isoformat()]
        added = self.rows("""SELECT SUM(o.invoice_total - COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.order_id = o.order_id AND p.payment_date <= o.order_date), 0))
            FROM orders o WHERE o.customer_id = ? AND o.payment_method = 'debt' AND o.status != 'cancelled' AND o.order_date BETWEEN ? AND ?""", [person] + window)[0][0]
        repaid = self.rows("""SELECT SUM(p.amount) FROM payments p JOIN orders o USING(order_id) WHERE o.customer_id = ? AND o.payment_method = 'debt'
            AND o.status != 'cancelled' AND p.payment_date > o.order_date AND p.payment_date BETWEEN ? AND ?""", [person] + window)[0][0]
        return {'type': 'list', 'answer': [money(added or 0), money(repaid or 0)]}

    def unpaid(self, select, end, tail=''):
        if 'payments' in self.capabilities:
            return self.rows(f"""SELECT {select} FROM (SELECT o.customer_id, o.order_date, o.invoice_total - COALESCE((SELECT SUM(p.amount) FROM payments p
                WHERE p.order_id = o.order_id AND p.payment_date <= ?), 0) AS owed FROM orders o
                WHERE o.status != 'cancelled' AND COALESCE(o.payment_method, '') != 'cod' AND o.order_date <= ?
                AND COALESCE(LOWER(o.channel), '') NOT IN ('shopee', 'lazada', 'tiktok', 'tiki', 'san_tmdt', 'marketplace')) WHERE owed > 0 {tail}""",
                             [end.isoformat(), end.isoformat()])
        if 'debt_snapshot' in self.capabilities and self.last and 0 <= (end - date.fromisoformat(self.last)).days <= 7:
            return self.rows(f"""SELECT {select} FROM (SELECT o.customer_id, o.order_date, COALESCE(e.debt_left, 0) AS owed
                FROM orders o LEFT JOIN order_extras e USING(order_id)
                WHERE o.status != 'cancelled' AND COALESCE(o.payment_method, '') != 'cod' AND o.order_date <= ?
                AND COALESCE(LOWER(o.channel), '') NOT IN ('shopee', 'lazada', 'tiktok', 'tiki', 'san_tmdt', 'marketplace')) WHERE owed > 0 {tail}""",
                             [end.isoformat()])
        raise Unsupported('shop has no payments')

    def metric_debt_total(self, slots, start, end, as_of):
        return {'type': 'number', 'answer': money(self.unpaid('SUM(owed)', end)[0][0] or 0)}

    def metric_debtors(self, slots, start, end, as_of):
        return {'type': 'number', 'answer': self.unpaid('COUNT(DISTINCT customer_id)', end)[0][0]}

    def metric_top_debtors(self, slots, start, end, as_of):
        rows = self.unpaid('customer_id, SUM(owed)', end, 'AND customer_id IS NOT NULL GROUP BY customer_id ORDER BY 2 DESC, customer_id')
        size = slots['top']
        if len(rows) < size or (len(rows) > size and rows[size - 1][1] == rows[size][1]):
            raise Unsupported('too few debtors or a tie at the cut')
        names = dict(self.rows('SELECT customer_id, customer_name FROM customers WHERE customer_id IN (' + ','.join('?' * size) + ')', [row[0] for row in rows[:size]]))
        return {'type': 'table', 'answer': {'columns': ['khach_hang', 'so_tien_no'], 'rows': [[names[row[0]], money(row[1])] for row in rows[:size]]}}

    def product_id(self, slots):
        found = self.rows('SELECT product_id FROM products WHERE product_name = ?', [slots['filters']['product']])
        if len(found) != 1:
            raise Unsupported('product name is not unique')
        return found[0][0]

    def metric_price_effect(self, slots, start, end, as_of):
        """Base units sold in the N days from the price change day against the N days before (rulebook section 5)."""
        product, days = self.product_id(slots), slots['days']

        def sold(first, last):
            return self.rows('SELECT SUM(basic_quantity) FROM sales WHERE product_id = ? AND order_date BETWEEN ? AND ?',
                             [product, first.isoformat(), last.isoformat()])[0][0] or 0
        before = sold(start - timedelta(days), start - timedelta(1))
        if not before:
            raise Unsupported('nothing sold before the change')
        return {'type': 'percent', 'answer': two(100 * (sold(start, start + timedelta(days - 1)) - before) / before)}

    def ledgers(self):
        """The rules' raw-column reader; a shop loaded without its raw tables cannot use it."""
        self.direct.open_books()
        if self.direct.ledgers is None:
            raise Unsupported('shop raw tables not loaded')
        return self.direct.ledgers

    def metric_stock_left(self, slots, start, end, as_of):
        """The stock rule's answer for the product the model names; the rule reads the day and units from the question."""
        product = next((p for p in self.direct.products if p[1] == slots['filters']['product']), None)
        found = self.ledgers().stock_left(slots['asked'], as_of, product) if product else None
        if found is None:
            raise Unsupported('stock left not read')
        return found

    def metric_gross_profit(self, slots, start, end, as_of):
        """Revenue less quantity times the cost on each sale line (the gross profit rule), in total or for the best item."""
        totals = self.ledgers().profit((start, end))
        if not totals:
            raise Unsupported('no cost on sale lines')
        if slots.get('item'):
            best = sorted(totals.items(), key=lambda item: -item[1])
            if len(best) > 1 and best[0][1] == best[1][1]:
                raise Unsupported('tie for the best item')
            return {'type': 'string', 'answer': best[0][0]}
        return {'type': 'number', 'answer': money(sum(totals.values()))}

    def metric_net_profit(self, slots, start, end, as_of):
        span = self.capped(start, end, as_of)
        totals = self.ledgers().profit(span)
        spent = self.ledgers().expenses(span)
        if not totals or spent is None:
            raise Unsupported('no cost or expense book')
        return {'type': 'number', 'answer': money(sum(totals.values()) - spent)}

    def metric_stock_value(self, slots, start, end, as_of):
        found = self.ledgers().stock_value(slots['asked'], as_of)
        if found is None:
            raise Unsupported('stock value not read')
        return found

    def metric_import_minus_sold(self, slots, start, end, as_of):
        product = next((p for p in self.direct.products if p[1] == slots['filters']['product']), None)
        found = self.ledgers().import_minus_sold_for(product, self.capped(start, end, as_of), minus=True) if product else None
        if found is None:
            raise Unsupported('import minus sold not read')
        return found

    def capped(self, start, end, as_of):
        return start, min(end, date.fromisoformat(as_of))

    def metric_staff_sales(self, slots, start, end, as_of):
        found = self.ledgers().staff_sales(slots['asked'], self.capped(start, end, as_of), slots['item'])
        if found is None:
            raise Unsupported('staff not recorded or not named')
        return found

    def metric_top_supplier(self, slots, start, end, as_of):
        self.need('purchase_prices')
        found = self.ledgers().top_supplier(self.capped(start, end, as_of), slots['main'])
        if found is None:
            raise Unsupported('supplier not recorded or tied')
        return found

    def metric_units_total(self, slots, start, end, as_of):
        self.need('line_items')
        asked = self.direct.units_asked(slots['asked'], strict=False)
        found = self.direct.units_sold(*asked, self.capped(start, end, as_of)) if asked else None
        if found is None:
            raise Unsupported('unit not read or nothing sold')
        return found

    def metric_debt_age(self, slots, start, end, as_of):
        found = self.direct.debt_by_age(slots['asked'], end)
        if found is None:
            raise Unsupported('debt age not read')
        return found

    def metric_price(self, slots, start, end, as_of):
        """The price written on a day's sale lines, for one base unit, cancelled and returned orders included (rulebook section 5)."""
        prices = self.rows('SELECT DISTINCT l.basic_unit_price FROM order_lines l JOIN orders o USING(order_id) WHERE l.product_id = ? AND o.order_date = ?',
                           [self.product_id(slots), start.isoformat()])
        if len(prices) != 1 or prices[0][0] is None:
            raise Unsupported('no single price that day')
        return {'type': 'number', 'answer': money(prices[0][0])}

    def metric_buy_price(self, slots, start, end, as_of):
        if 'purchase_prices' not in self.capabilities and not self.rows('SELECT 1 FROM purchase_lines WHERE COALESCE(recorded_total, unit_price) IS NOT NULL LIMIT 1'):
            return {'type': 'unanswerable', 'reason': 'shop does not record purchase prices'}
        from datamate.normalized import name_key
        pid = self.product_id(slots)
        all_rows = self.rows('SELECT purchase_unit, source_status, COALESCE(recorded_total, quantity * unit_price), quantity FROM purchase_lines '
                             'WHERE product_id = ? AND import_date BETWEEN ? AND ?', [pid, start.isoformat(), end.isoformat()])
        valid = [r for r in all_rows if 'huy' not in name_key(r[1] or '', accents=True) and r[2] is not None and (r[3] or 0) > 0]
        rows = [r for r in valid if name_key(r[0] or '', accents=True) == slots['unit']]
        if rows:
            quantity = sum(r[3] for r in rows)
            return {'type': 'number', 'answer': money(sum(r[2] for r in rows) / quantity)}
        if valid:
            base = self.rows('SELECT base_unit FROM products WHERE product_id = ?', [pid])[0][0]
            factors = {name_key(u or '', accents=True): f for u, f in self.rows('SELECT sale_unit, factor FROM unit_conversions WHERE product_id = ?', [pid]) if f}
            if base:
                factors.setdefault(name_key(base, accents=True), 1.0)
            target_f = factors.get(slots['unit'])
            if target_f and all(name_key(r[0] or '', accents=True) in factors for r in valid):
                base_qty = sum(r[3] * factors[name_key(r[0] or '', accents=True)] for r in valid)
                if base_qty:
                    return {'type': 'number', 'answer': money(sum(r[2] for r in valid) / (base_qty / target_f))}
        raise Unsupported('nothing imported in that unit')

    def month_totals(self, slots):
        return [self.revenue(*month_range(y, m), None) for m, y in slots['months']]

    def metric_monthly(self, slots, start, end, as_of):
        return {'type': 'list', 'answer': [money(total) for total in self.month_totals(slots)]}

    def metric_rising(self, slots, start, end, as_of):
        ordered = [total for _, total in sorted(zip([y * 12 + m for m, y in slots['months']], self.month_totals(slots)))]
        return {'type': 'boolean', 'answer': all(b > a for a, b in zip(ordered, ordered[1:]))}

    def metric_best_month(self, slots, start, end, as_of):
        totals = sorted(zip(self.month_totals(slots), [f'{y}-{m:02}' for m, y in slots['months']]), reverse=True)
        if totals[0][0] == totals[1][0]:
            raise Unsupported('tie for the best month')
        return {'type': 'date', 'answer': totals[0][1]}


def listed_months(plain, as_of):
    """The months a list question names, as the rules read them: a run, a range, or N months ending at one."""
    from datamate.direct import Direct, named_months
    months = named_months(plain)
    back = re.search(r'(\d+) thang (?:lien nhau|lien tiep) (?:ket thuc|tinh toi|tinh den|den)', plain)
    if back and len(set(months)) == 1:
        index = months[0][1] * 12 + months[0][0] - 1
        return [(i % 12 + 1, i // 12) for i in range(index - int(back[1]) + 1, index + 1)]
    if len(months) == 2 and re.search(r'\btu thang \d+/\d+ (?:den|toi|qua|sang) (?:het )?thang|thang \d+/\d+ (?:den|toi) (?:het )?thang', plain):
        low, high = months[0][1] * 12 + months[0][0] - 1, months[1][1] * 12 + months[1][0] - 1
        return [(i % 12 + 1, i // 12) for i in range(low, high + 1)]
    found = Direct.month_list(plain, as_of) or months
    return found if len(found) >= 2 and len(set(found)) == len(found) else None
