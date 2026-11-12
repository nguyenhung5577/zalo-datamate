"""Rules for shop books the common tables do not hold: repair logs, phone trade-ins, installments, warranty, baby-shop fees and sizes.
Books are recognized by their columns and read from the raw tables; each rule follows the rulebook or declines."""
import calendar
from datetime import date
import re
import sqlite3

from datamate.direct import HOW_MANY, any_period
from datamate.metrics import money, two
from datamate.normalized import name_key, parse_date, parse_number

PERCENT = r'%|phan tram|ti le|ty le|chiem'
JOBS = r'nhot|\bthay\b|\bva\b|\bvo\b|\blap\b|\bdien\b|\bden\b|\bbo\b|\bnoi\b|bugi|bu gi|cu ?roa|ruot|kim phun|bao duong|\bdong\b|lead|vision|wave|air ?blade|sirius|exciter|\bsh\b'
MODELS = [(r'\b(?:air ?blade|ab)\b', 'Air Blade'), (r'\bsh ?mode\b', 'SH Mode'), (r'\b(?:wave )?rsx\b', 'Wave RSX'), (r'\b(?:wave )?(?:alpha|α)\b|\bwave a\b', 'Wave Alpha'), (r'\b(?:exciter|ex)\b', 'Exciter'),
          (r'\bsirius fi\b', 'Sirius FI'), (r'\bsirius\b', 'Sirius'), (r'\bvision\b', 'Vision'), (r'\blead\b', 'Lead'), (r'\bfuture\b', 'Future'), (r'\bvario\b', 'Vario'), (r'\bjanus\b', 'Janus'), (r'\bgrande\b', 'Grande'), (r'\bwinner x\b', 'Winner X'), (r'\bwinner\b', 'Winner')]
# What the question says, then how the job cell says it. Bare nhot is engine oil and bare ruot a new tube (rulebook).
JOB_WORDS = [(r'\bva ruot\b|\bva xe\b', r'\bva\b'), (r'\bdau lap\b|\bnhot lap\b|\bnhot hop so\b', r'\blap\b|\bhop so\b'), (r'\bsua dien\b|\blam dien\b', r'\bsua dien\b|\blam dien\b')]
WEEKDAYS = {'hai': 0, '2': 0, 'ba': 1, '3': 1, 'tu': 2, '4': 2, 'nam': 3, '5': 3, 'sau': 4, '6': 4, 'bay': 5, '7': 5}


def key(value):
    return name_key(value or '', accents=True)


def number(value):
    try:
        return parse_number(value)
    except ValueError:
        return None


def due_date(bought, step, day):
    """The due date of installment `step`, counted from the month after purchase."""
    index = bought.year * 12 + bought.month - 1 + step
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


class Contract:
    def __init__(self, customer, bought, price, prepaid, count, monthly, day):
        self.customer, self.bought, self.price, self.prepaid = customer, bought, price, prepaid
        self.count, self.monthly, self.day = count, monthly, day
        self.paid = []

    def paid_by(self, day):
        return sum(amount for when, amount in self.paid if when <= day)

    def remaining(self, day):
        return self.price - self.prepaid - self.paid_by(day)

    def due_steps(self, day, strict=False):
        """Installments due by `day`; a due date on the day itself is not overdue, so `strict` leaves it out."""
        steps = 0
        for step in range(1, self.count + 1):
            due = due_date(self.bought, step, self.day)
            if due < day or (due == day and not strict):
                steps += 1
        return steps

    def overdue(self, day):
        return max(0, min(self.due_steps(day, strict=True) * self.monthly, self.price - self.prepaid) - self.paid_by(day))


class Books:
    def __init__(self, direct):
        self.direct, self.metrics = direct, direct.metrics
        self.raw = getattr(direct.metrics.data, 'raw', None)
        self.columns, self.tables = {}, {}
        if self.raw is not None:
            # The guarded query refuses whole tables, so books are read straight from the read-only copy, only when asked for.
            self.connection = sqlite3.connect(f'file:{self.raw.path}?mode=ro', uri=True)
            names = [row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            self.columns = {name: {row[1] for row in self.connection.execute(f'PRAGMA table_info("{name}")')} for name in names}
        self.status, self.days = {}, {}
        if self.raw is not None:
            orders = sqlite3.connect(f'file:{self.metrics.data.path}?mode=ro', uri=True).execute('SELECT order_id, status, order_date FROM orders')
            for order, status, day in orders:
                self.status[str(order)], self.days[str(order)] = status, day

    def table(self, *columns):
        rows = []
        for name, known in self.columns.items():
            if set(columns) <= known:
                if name not in self.tables:
                    cursor = self.connection.execute(f'SELECT * FROM "{name}"')
                    titles = [item[0] for item in cursor.description]
                    self.tables[name] = [dict(zip(titles, row)) for row in cursor]
                rows += self.tables[name]
        return rows

    def answer(self, plain, as_of):
        plain = re.sub(r'\bvo sua\b', 'toi sua', plain)
        for rule in (self.repair_jobs, self.book_debts, self.phone_averages, self.phone_warranty_sold, self.milk_brand, self.repair, self.installments, self.phone_warranty, self.trade_in, self.customer_pays, self.phones, self.fees, self.sizes, self.new_members):
            try:
                found = rule(plain, as_of)
            except (ValueError, TypeError, KeyError, ZeroDivisionError):
                return None
            if found is not None:
                return found
        return None

    def eligible(self, order, span):
        """An order counted under rulebook section 1 and placed inside the period."""
        return self.status.get(order) in {'completed', 'shipping'} and span[0].isoformat() <= self.days.get(order, '') <= span[1].isoformat()

    # Repair log

    def tickets(self):
        found = []
        for row in self.table('Xe', 'Làm gì', 'Thợ', 'Tiền'):
            if not row['Ngày'] or not row['Xe']:
                continue
            plate = re.search(r'\d{2}[a-zA-Z]\d?-?[\d.]+', row['Xe'])
            found.append({'day': date.fromisoformat(parse_date(row['Ngày'], row.get('__dm_sheet') or '')), 'plate': plate[0].upper() if plate else row['Xe'],
                          'who': key(row['Thợ']).split()[-1].split('.')[-1] if row['Thợ'] else None, 'money': number(row['Tiền']) or 0,
                          'warranty': 'bao hanh' in key(row['Ghi chú'])})
        return found

    def mechanics(self):
        return {key(row['Họ tên']): (row['Họ tên'], number(row['Hoa hồng (%)'])) for row in self.table('Họ tên', 'Hoa hồng (%)')}

    def repair(self, plain, as_of):
        mechanics = self.mechanics()
        if not mechanics or re.search(JOBS, re.sub(r'\bden het\b', ' ', plain)):
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        short = {}
        for full in mechanics:
            short.setdefault(full.split()[-1], []).append(full)
        if any(len(names) > 1 for names in short.values()):
            return None
        rows = [t for t in self.tickets() if span[0] <= t['day'] <= span[1]]
        paid = [t for t in rows if not t['warranty']]
        counted = lambda r: sum(t['money'] for t in r)
        named = [full for full in mechanics if full in plain]
        if len(named) > 1:
            return None
        if named:
            mine = [t for t in rows if t['who'] == named[0].split()[-1]]
            if re.search(r'hoa hong|hue hong', plain):
                return {'type': 'number', 'answer': money(counted(mine) * mechanics[named[0]][1] / 100)}
            if re.search(r'\btien\b|dthu|doanh thu', plain):
                return {'type': 'number', 'answer': money(counted(mine))}
            if re.search(r'luot|phieu', plain):
                return {'type': 'number', 'answer': len(mine)}
            return None
        if re.search(r'\btho\b', plain) and re.search(r'nhat|nhut', plain):
            scores = {}
            for t in rows:
                scores.setdefault(t['who'], []).append(t)
            if re.search(r'cham chi', plain):
                value = lambda r: len({t['day'] for t in r})
            elif re.search(r'hoa hong|hue hong', plain):
                value = lambda r: counted(r) * mechanics[short[r[0]['who']][0]][1] if r[0]['who'] in short else 0
            elif re.search(r'\btien\b|dthu|doanh thu', plain):
                value = counted
            elif re.search(r'luot|phieu', plain):
                value = len
            else:
                return None
            ranked = sorted(((value(r), who) for who, r in scores.items() if who in short), reverse=True)
            if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
                return None
            return {'type': 'string', 'answer': mechanics[short[ranked[0][1]][0]][0]}
        if 'bao hanh' in plain:
            if re.search(PERCENT, plain):
                return {'type': 'percent', 'answer': two(100 * (len(rows) - len(paid)) / len(paid))} if paid else None
            if re.search(HOW_MANY + r' (?:phieu|luot)', plain) and not re.search(r'khong tinh|bo ', plain):
                return {'type': 'number', 'answer': len(rows) - len(paid)}
            if not re.search(r'khong tinh|bo ', plain):
                return None
        if re.search(r'binh quan|trung binh', plain):
            weekday = re.search(r'\bthu (hai|ba|tu|nam|sau|bay|[2-7])\b', plain)
            if weekday and re.search(r'luot|phieu', plain):
                index = WEEKDAYS[weekday[1]]
                count = sum(1 for offset in range((span[1] - span[0]).days + 1) if date.fromordinal(span[0].toordinal() + offset).weekday() == index)
                return {'type': 'number', 'answer': two(sum(t['day'].weekday() == index for t in rows) / count)} if count else None
            if re.search(r'phieu', plain) and re.search(r'\btien\b|bnhiu|bao nhieu', plain) and not weekday:
                return {'type': 'number', 'answer': money(counted(paid) / len(paid))} if paid else None
            return None
        twice = re.search(r'(?:tu |it nhat )(\d+) (?:lan|phieu)', plain)
        if re.search(HOW_MANY + r' (?:chiec )?xe', plain):
            if twice:
                visits = {}
                for t in paid:
                    visits[t['plate']] = visits.get(t['plate'], 0) + 1
                return {'type': 'number', 'answer': sum(v >= int(twice[1]) for v in visits.values())}
            if re.search(r'khac nhau', plain):
                return {'type': 'number', 'answer': len({t['plate'] for t in rows})}
            return None
        if re.search(HOW_MANY + r' (?:phieu|luot)|(?:phieu|luot) sua', plain) and re.search(HOW_MANY + r'|dem', plain):
            return {'type': 'number', 'answer': len(rows)}
        return None

    def repair_jobs(self, plain, as_of):
        """Questions about one job, one bike model, or everyone's commission (rulebook, repair shop)."""
        mechanics = self.mechanics()
        span = any_period(plain, as_of) if mechanics else None
        if span is None:
            return None
        rows = [t for t in self.tickets_with_jobs() if span[0] <= t['day'] <= span[1]]
        if re.search(r'hoa hong|hue hong', plain) and re.search(r'\bbang\b|tung tho|moi tho|cac tho', plain):
            short = {full.split()[-1]: full for full in mechanics}
            if len(short) != len(mechanics):
                return None
            earned = {full: 0 for full in mechanics}
            for t in rows:
                if t['who'] in short:
                    earned[short[t['who']]] += t['money'] * mechanics[short[t['who']]][1] / 100
            table = sorted(((mechanics[full][0], money(value)) for full, value in earned.items()), key=lambda row: -row[1])
            return {'type': 'table', 'answer': {'columns': ['tho', 'hoa_hong'], 'rows': [list(row) for row in table]}}
        model = next((name for pattern, name in MODELS if re.search(pattern, plain)), None)
        if model and re.search(r'khac nhau|moi xe tinh mot lan', plain) and re.search(HOW_MANY + r' (?:chiec )?(?:xe)?', plain):
            return {'type': 'number', 'answer': len({t['plate'] for t in rows if t['model'] == model})}
        if re.search(r'\bdong xe\b', plain) and re.search(r'nhieu tien', plain) and re.search(r'nhat|nhut', plain):
            totals = {}
            for t in rows:
                totals[t['model']] = totals.get(t['model'], 0) + t['money']
            best = sorted(totals.items(), key=lambda item: -item[1])
            return {'type': 'string', 'answer': best[0][0]} if best and (len(best) == 1 or best[0][1] != best[1][1]) else None
        job = next((pattern for asked, pattern in JOB_WORDS if re.search(asked, plain)), None)
        if job and re.search(r'luot|phieu', plain) and re.search(HOW_MANY + r'|\bdem\b', plain) and not re.search(r'%|phan tram|\btien\b', plain):
            # A warranty ticket does not count as doing the job (rulebook).
            return {'type': 'number', 'answer': sum(1 for t in rows if not t['warranty'] and re.search(job, t['job']))}
        return None

    def tickets_with_jobs(self):
        found = []
        rows = self.table('Xe', 'Làm gì', 'Thợ', 'Tiền')
        for ticket, row in zip(self.tickets(), [r for r in rows if r['Ngày'] and r['Xe']]):
            bike = key(re.sub(r'\s*\d{2}[a-zA-Z]\d?-?[\d.]+.*', '', row['Xe']))
            found.append({**ticket, 'job': key(row['Làm gì']), 'model': next((name for pattern, name in MODELS if re.search(pattern, bike)), bike)})
        return found

    # Phone shop

    def debts_at(self, day):
        """Unpaid money at the end of a day as (customer, bought, owed), from the debt book and the installment book (rulebook: both are debt)."""
        entries = []
        for row in self.table('Nợ', 'Đã trả', 'Ngày trả'):
            if row['Ngày']:
                raw_note = str(row.get('Ghi chú') or '').strip()
                stt = re.search(r'\bstt\s*(\d+)\b', raw_note, flags=re.I)
                ref = stt[1] if stt else raw_note
                cancelled = bool(ref and self.status.get(ref) == 'cancelled')
                bought = date.fromisoformat(parse_date(row['Ngày']))
                entries.append([key(row['Khách']), bought, number(row['Nợ']) or 0, cancelled])
            if entries and not entries[-1][3] and entries[-1][1] <= day:
                if row['Ngày trả'] and date.fromisoformat(parse_date(row['Ngày trả'])) <= day:
                    entries[-1][2] -= number(row['Đã trả']) or 0
        found = [(cust, bought, owed) for cust, bought, owed, cancelled in entries if not cancelled and bought <= day]
        found += [(c.customer, c.bought, c.remaining(day)) for c in self.contracts() if c.bought <= day]
        return [row for row in found if row[2] > 0]

    def book_debts(self, plain, as_of):
        if not self.table('Góp/tháng') or not re.search(r'\b(?:con|kon) no\b|\bcong no\b', plain) or re.search(r'\bnao\b|\bnhat\b|qua han|\bky gop\b', plain):
            return None
        days = re.findall(r'(\d{1,2})/(\d{1,2})/(\d{4})', plain)
        if len(days) == 1:
            day = date(int(days[0][2]), int(days[0][1]), int(days[0][0]))
        elif not days and re.search(r'hom nay|hnay|bua nay|bua ni', plain):
            day = date.fromisoformat(as_of)
        else:
            return None
        debts = self.debts_at(day)
        if re.search(r'tuoi no', plain) and re.search(r'0\D{1,3}7', plain) and re.search(r'8\D{1,3}15', plain):
            ages = [(day - bought).days for _, bought, _ in debts]
            buckets = [lambda a: a <= 7, lambda a: 8 <= a <= 15, lambda a: a > 15]
            return {'type': 'list', 'answer': [money(sum(d[2] for d, a in zip(debts, ages) if test(a))) for test in buckets]}
        if re.search(HOW_MANY + r' (?:khach|nguoi)', plain):
            return {'type': 'number', 'answer': len({d[0] for d in debts})}
        return {'type': 'number', 'answer': money(sum(d[2] for d in debts))} if re.search(r'\btien\b|\btong\b', plain) else None

    def phone_averages(self, plain, as_of):
        """What one phone brings in on average, and the best seller of one kind."""
        kinds = self.sold_kinds()
        span = any_period(plain, as_of) if kinds else None
        if span is None:
            return None
        rows = [(name, qty or 0, revenue or 0, kinds.get(name)) for name, qty, revenue in self.sold(span)]
        group = re.search(r'nhom (may moi|may cu|phu kien)', plain)
        if group and re.search(r'\bnao\b', plain) and re.search(r'nhat|nhut', plain):
            ranked = sorted((r for r in rows if r[3] == group[1]), key=lambda r: -(r[2] if 'tien' in plain else r[1]))
            return {'type': 'string', 'answer': ranked[0][0]} if ranked and (len(ranked) == 1 or ranked[0][1:3] != ranked[1][1:3]) else None
        if re.search(r'trung binh', plain) and re.search(r'\bmay\b', plain) and re.search(r'\bgia\b|\btien\b', plain):
            phones = [r for r in rows if r[3] in ('may moi', 'may cu')]
            count = sum(r[1] for r in phones)
            return {'type': 'number', 'answer': money(sum(r[2] for r in phones) / count)} if count else None
        return None

    def phone_warranty_sold(self, plain, as_of):
        rows = self.table('Ngày nhận', 'IMEI', 'Máy', 'Xử lý')
        if not rows or 'bao hanh' not in plain:
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        if re.search(r'\bdong may\b', plain) and re.search(r'nhat|nhut', plain):
            lines = {}
            for row in rows:
                if row['Ngày nhận'] and span[0].isoformat() <= parse_date(row['Ngày nhận']) <= span[1].isoformat():
                    line = re.split(r'\s*\d+\s*GB', row['Máy'])[0]
                    lines[line] = lines.get(line, 0) + 1
            best = sorted(lines.items(), key=lambda item: -item[1])
            return {'type': 'string', 'answer': best[0][0]} if best and (len(best) == 1 or best[0][1] != best[1][1]) else None
        if re.search(r'\b(?:may )?ban (?:ra )?(?:trong )?thang', plain) and re.search(HOW_MANY + r' may', plain):
            return {'type': 'number', 'answer': len({row['IMEI'] for row in rows if row['Ngày mua'] and row['Ngày nhận'] and span[0].isoformat() <= parse_date(row['Ngày mua']) <= span[1].isoformat() and parse_date(row['Ngày nhận']) <= as_of})}
        return None

    def milk_brand(self, plain, as_of):
        """The brand selling most of one milk number; the accent that marks brand is gone by now, so the direct reader says it was asked."""
        milk = re.search(r'sua bot so (\d)', plain)
        if not milk or not self.direct.brand_asked or not re.search(r'\bnao\b', plain) or not self.catalog():
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        sold = {name: qty or 0 for name, qty, _ in self.sold(span)}
        brands = {}
        for row in self.catalog():
            if key(row['Nhóm']) == 'sua bot' and key(row['Số / Size']) == 'so ' + milk[1] and row['Hãng']:
                brands[row['Hãng']] = brands.get(row['Hãng'], 0) + sold.get(row['Tên hàng'], 0)
        best = sorted(brands.items(), key=lambda item: -item[1])
        return {'type': 'string', 'answer': best[0][0]} if best and (len(best) == 1 or best[0][1] != best[1][1]) else None

    def contracts(self):
        rows, found = self.table('Số kỳ', 'Góp/tháng', 'Đã góp'), {}
        for row in rows:
            order = row['STT']
            if order is None:
                continue
            if row['Số kỳ']:
                if self.status.get(str(order)) == 'cancelled':
                    continue
                found[order] = Contract(key(row['Khách']), date.fromisoformat(parse_date(row['Ngày'])), number(row['Giá']), number(row['Trả trước']) or 0,
                                        int(number(row['Số kỳ'])), number(row['Góp/tháng']), int(number(row['Hạn góp (ngày)'])))
            if order in found and row['Ngày góp'] and row['Đã góp']:
                found[order].paid.append((date.fromisoformat(parse_date(row['Ngày góp'])), number(row['Đã góp'])))
        return list(found.values())

    def installments(self, plain, as_of):
        if not re.search(r'\bgop\b|ky han|qua han', plain):
            return None
        contracts = self.contracts()
        if not contracts:
            return None
        day = re.findall(r'(\d{1,2})/(\d{1,2})/(\d{4})', plain)
        if len(day) > 1:
            return None
        when = date(int(day[0][2]), int(day[0][1]), int(day[0][0])) if day else date.fromisoformat(as_of)
        if not day and not re.search(r'hom nay|hnay|bua nay|bua ni', plain):
            return None
        held = [c for c in contracts if c.bought <= when]
        people = [name for name in {c.customer for c in contracts} if name in plain]
        if len(people) > 1:
            return None
        if people:
            held = [c for c in held if c.customer == people[0]]
        if re.search(r'qua han|cham gop', plain):
            late = [c for c in held if c.overdue(when) > 0]
            if re.search(r'\btien\b', plain):
                return {'type': 'number', 'answer': money(sum(c.overdue(when) for c in held))}
            if re.search(r'(?:' + HOW_MANY + r'|\bmi) (?:khach|nguoi|ngui|ngoi)|khach qua han', plain):
                return {'type': 'number', 'answer': len({c.customer for c in late})}
            return None
        if re.search(r'tinh ca|ke ca|chua toi han|con lai|chua gop|chua tra|con thieu', plain) and re.search(r'\btien\b|bao nhieu|bnhiu', plain):
            if re.search(r'tinh ca|ke ca|chua toi han', plain) or re.search(r'con lai|chua gop|chua tra|con thieu', plain):
                return {'type': 'number', 'answer': money(sum(max(0, c.remaining(when)) for c in held))}
        if people and re.search(r'may ky|mi ky|bao nhieu ky|bnhiu ky', plain) and re.search(r'toi han|den han', plain):
            return {'type': 'number', 'answer': sum(c.due_steps(when) for c in held)}
        return None

    def sold_kinds(self):
        """Product name to its kind (may moi, may cu, phu kien) from the short-name sheet."""
        return {row['Tên hàng']: key(row['Loại']) for row in self.table('Ghi tắt', 'Tên hàng', 'Loại') if row['Tên hàng'] and row['Loại']}

    def sold(self, span):
        return self.metrics.rows('SELECT product_name, SUM(basic_quantity), SUM(line_revenue) FROM sales WHERE order_date BETWEEN ? AND ? GROUP BY product_name',
                                 [span[0].isoformat(), span[1].isoformat()])

    def phones(self, plain, as_of):
        kinds = self.sold_kinds()
        if not kinds or re.search(r'nhap (?!them)|\bton\b|bao hanh|\bgop\b(?! ca)|(?<!doanh )thu may|thu mua|thu lai', plain):
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        rows = [(name, qty or 0, revenue or 0, kinds.get(name)) for name, qty, revenue in self.sold(span)]
        if any(kind is None for *_, kind in rows):
            return None
        phones = [r for r in rows if r[3] != 'phu kien']
        if re.search(PERCENT, plain):
            if re.search(r'may moi', plain) and re.search(r'tien|dthu|doanh thu', plain) and re.search(r'may\b', plain):
                whole = sum(r[2] for r in phones)
                return {'type': 'percent', 'answer': two(100 * sum(r[2] for r in phones if r[3] == 'may moi') / whole)} if whole else None
            brand = re.search(r'\bmay (apple|iphone|samsung|xiaomi|oppo|vivo|realme|nokia)\b', plain)
            if brand and re.search(r'tong so may', plain):
                want = 'iphone' if brand[1] == 'apple' else brand[1]
                whole = sum(r[1] for r in phones)
                return {'type': 'percent', 'answer': two(100 * sum(r[1] for r in phones if key(r[0]).split()[0] == want) / whole)} if whole else None
            return None
        group = re.search(r'nhom (may moi|may cu|phu kien)', plain)
        if group and re.search(r'\btien\b|dthu|doanh thu', plain):
            return {'type': 'number', 'answer': money(sum(r[2] for r in rows if r[3] == group[1]))}
        if re.search(r'\bdong may\b', plain) and re.search(r'nhat|nhut|\btop \d', plain) and not re.search(r'it nhat|thap nhat|cham nhat', plain):
            by_rev = bool(re.search(r'nhi?e?u tien|doanh thu|dthu', plain))
            totals = {}
            for name, qty, rev, _ in phones:
                line = re.split(r'\s*\d+\s*GB', name, flags=re.I)[0].strip()
                totals[line] = totals.get(line, 0) + (rev if by_rev else qty)
            ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
            top_k = re.search(r'(?:top |(?<![/\w-]))(\d{1,2}) dong may\b', plain) or re.search(r'\btop (\d+)', plain)
            if top_k:
                size = int(top_k[1])
                if len(ranked) >= size and (len(ranked) == size or ranked[size - 1][1] != ranked[size][1]):
                    return {'type': 'list', 'answer': [item[0] for item in ranked[:size]]}
                return None
            if re.search(r'dong may (?:nao|gi|chi)', plain) and ranked and (len(ranked) == 1 or ranked[0][1] != ranked[1][1]):
                return {'type': 'string', 'answer': ranked[0][0]}
            return None
        if re.search(r'\bnao\b|\bmo\b|nhat|nhut', plain) or not re.search(HOW_MANY + r' (?:may|chiec|cai|dien thoai)\b|\bnhieu (?:may|chiec|cai|dien thoai)\b', plain):
            return None
        lines = {}
        for name, qty, _, kind in rows:
            lines.setdefault(key(re.split(r'\s*\d+\s*GB', name)[0]), []).append((name, qty, kind))
        every = {key(re.split(r'\s*\d+\s*GB', name)[0]) for name in kinds}
        words = ' ' + ' '.join(re.findall(r'[\w.%/+-]+', plain)) + ' '
        named = [line for line in every if ' ' + line + ' ' in words]
        if named:
            longest = max(named, key=len)
            return {'type': 'number', 'answer': sum(item[1] for item in lines.get(longest, []))}
        if re.search(r'may cu|dien thoai cu', plain) and not re.search(r'may moi', plain):
            return {'type': 'number', 'answer': sum(r[1] for r in rows if r[3] == 'may cu')}
        if re.search(r'may moi', plain) and not re.search(r'may cu', plain):
            return {'type': 'number', 'answer': sum(r[1] for r in rows if r[3] == 'may moi')}
        if re.search(r'phu kien|dien thoai|may moi', plain):
            return {'type': 'number', 'answer': sum(r[1] for r in phones)}
        return None

    def phone_warranty(self, plain, as_of):
        rows = self.table('Ngày nhận', 'IMEI', 'Máy', 'Xử lý')
        if not rows or 'bao hanh' not in plain or re.search(r'nhat|nhut|\bdong may\b', plain):
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        count = sum(1 for row in rows if row['Ngày nhận'] and span[0].isoformat() <= parse_date(row['Ngày nhận']) <= span[1].isoformat())
        if re.search(PERCENT, plain):
            kinds = self.sold_kinds()
            sold = sum(qty or 0 for name, qty, _ in self.sold(span) if kinds.get(name, 'phu kien') != 'phu kien')
            return {'type': 'percent', 'answer': two(100 * count / sold)} if sold else None
        if re.search(HOW_MANY + r' luot|luot may', plain) and not re.search(r'it nhat', plain):
            return {'type': 'number', 'answer': count}
        return None

    def order_rows(self):
        """Sales-book rows per order with the order number carried down; the sheets leave it blank on later lines."""
        last, found = None, {}
        for row in self.table('STT', 'Hàng', 'Khách trả'):
            last = row['STT'] or last
            if last is not None:
                found.setdefault(str(last), []).append(row)
        return found

    def first_value(self, rows, column):
        for row in rows:
            value = number(row.get(column))
            if value is not None:
                return value
        return 0

    def trade_ins(self, span):
        """Trade-in money per counted order, from the sales-book column or the separate book."""
        found = {}
        separate = self.table('Máy thu', 'Giá thu', 'Trừ vào (STT sổ bán)')
        for row in separate:
            order = str(row['Trừ vào (STT sổ bán)'] or '').strip()
            if self.eligible(order, span):
                found[order] = found.get(order, 0) + (number(row['Giá thu']) or 0)
        if not separate:
            for order, rows in self.order_rows().items():
                value = self.first_value(rows, 'Thu máy cũ')
                if value and self.eligible(order, span):
                    found[order] = value
        return found

    def trade_in(self, plain, as_of):
        if not re.search(r'(?:thu|thu mua|thu lai|tien) may cu|may cu thu lai', plain) or re.search(r'phai tra', plain):
            return None
        if not self.order_rows():
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        found = self.trade_ins(span)
        if re.search(PERCENT, plain):
            total = sum(1 for order in self.status if self.eligible(order, span))
            return {'type': 'percent', 'answer': two(100 * len(found) / total)} if total else None
        if re.search(HOW_MANY + r' (?:don|dh)\b', plain):
            return {'type': 'number', 'answer': len(found)}
        return {'type': 'number', 'answer': money(sum(found.values()))} if re.search(r'\btien\b|bao nhieu|bnhiu|tong|nhieu', plain) else None

    def customer_pays(self, plain, as_of):
        if not re.search(r'khach ph[ai]+ tra', plain):
            return None
        span = any_period(plain, as_of)
        orders = self.order_rows()
        if span is None or not orders:
            return None
        paid = [self.first_value(rows, 'Khách trả') for order, rows in orders.items() if self.eligible(order, span)]
        if re.search(r'trung binh|binh quan', plain):
            return {'type': 'number', 'answer': money(sum(paid) / len(paid))} if paid else None
        return {'type': 'number', 'answer': money(sum(paid))}

    # Baby shop

    def fee_rows(self):
        seen, found = set(), []
        for row in self.table('HĐ', 'Tên hàng', 'TT', 'ĐVT'):
            label = key(row['Tên hàng'])
            kind = 'voucher' if label == 'voucher' else 'points' if re.match(r'dung \d+ diem', label) else 'ship' if label == 'phi ship' else None
            identity = (row['HĐ'], row['Tên hàng'], row['TT'])
            if kind and identity not in seen:
                seen.add(identity)
                found.append((str(row['HĐ']), kind, abs(number(row['TT']) or 0)))
        return found

    def fees(self, plain, as_of):
        kind = 'voucher' if re.search(r'voucher|vau cho|vau cho', plain) else 'ship' if re.search(r'phi ship|phi giao', plain) else 'points' if re.search(r'\bdiem\b', plain) else None
        if kind is None or re.search(r'nhat|nhut|%|phan tram', plain):
            return None
        rows = self.fee_rows()
        span = any_period(plain, as_of)
        if not rows or span is None:
            return None
        return {'type': 'number', 'answer': money(sum(amount for order, name, amount in rows if name == kind and self.eligible(order, span)))}

    def catalog(self):
        return [row for row in self.table('Tên hàng', 'Nhóm', 'Hãng', 'Số / Size') if row['Tên hàng']]

    def sizes(self, plain, as_of):
        catalog = self.catalog()
        if not catalog or re.search(r'nhat|nhut', plain):
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        sold = {name: qty or 0 for name, qty, _ in self.sold(span)}
        bags = [row for row in catalog if key(row['Nhóm']) == 'bim ta']
        total = sum(sold.get(row['Tên hàng'], 0) for row in bags)
        if re.search(r'\bbim\b', plain):
            size = re.search(r'size (\w+)', plain)
            brands = {key(row['Hãng']) for row in bags if row['Hãng']}
            named = [brand for brand in brands if re.search(r'\bbim ' + re.escape(brand) + r'\b', plain)]
            if size and not named:
                count = sum(sold.get(row['Tên hàng'], 0) for row in bags if key(row['Số / Size']) in {size[1], 'size ' + size[1]})
            elif named and len(named) == 1 and not size:
                count = sum(sold.get(row['Tên hàng'], 0) for row in bags if key(row['Hãng']) == named[0])
            else:
                return None
            if re.search(PERCENT, plain):
                return {'type': 'percent', 'answer': two(100 * count / total)} if total else None
            return {'type': 'number', 'answer': count} if re.search(HOW_MANY + r' bich', plain) else None
        milk = re.search(r'sua bot so (\d)', plain)
        if milk and re.search(HOW_MANY + r' lon', plain):
            return {'type': 'number', 'answer': sum(sold.get(row['Tên hàng'], 0) for row in catalog if key(row['Nhóm']) == 'sua bot' and key(row['Số / Size']) == 'so ' + milk[1])}
        return None

    def new_members(self, plain, as_of):
        rows = self.table('Tên KH', 'Ngày đăng ký')
        if not rows or not re.search(r'thanh vien moi|khach moi dang ky|dang ky moi|dang ky thanh vien', plain):
            return None
        span = any_period(plain, as_of)
        if span is None or not re.search(HOW_MANY + r'|\bso khach\b', plain):
            return None
        return {'type': 'number', 'answer': sum(1 for row in rows if row['Ngày đăng ký'] and span[0].isoformat() <= parse_date(row['Ngày đăng ký']) <= span[1].isoformat())}
