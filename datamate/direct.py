"""Question families answered by code alone, with no model: each rule reads the whole question or declines."""
import re
import sqlite3

from datetime import date

from datamate.metrics import Unsupported, fix_period, month_range, money, resolve_period, standard_months, two
from datamate.normalized import name_key
from datamate.provenance import Withheld, switch_off

# Any of these changes what is being asked, so the plain rules below must not answer.
CONDITIONS = (r'\b(?:ca sang|ca chieu|tai quay|dien thoai|zalo|shopee|lazada|tiktok|tien mat|chuyen khoan|cod|vi|ghi no|nhom|khach|nhan vien'
              r'|dot|tro len|tren \d+|duoi \d+|it nhat|huy|tra lai|size|tet|cuoi tuan|(?<!doanh )thu \d|chu nhat|hon|kem|chenh|lai|loi|may moi|may cu)\b')
FILTERS = (r'\b(?:ca sang|ca chieu|tai quay|dien thoai|zalo|shopee|lazada|tiktok|tien mat|chuyen khoan|cod|nhom|nhan vien|cuoi tuan|chu nhat|(?<!doanh )thu \d)\b')
UNITLESS = {'phan', 'la', 'nam', 'thang', 'ngay', 'hang', 'mon', 'mat', 'cua', 'ban', 'tien'}
HOW_MANY = r'(?:may|bao nhieu|bao nhiu|bnhiu|bn)'
ITEM = r'(?:mat hang|san pham|sp|mon|hang)'
SPENT = r'doanh thu|dthu|tien hang|nhi?e?u tien'
# A word right after the unit that is not one of these names a product we could not match, so the total must not answer.
GENERIC = set('het thi luon moi lon ly bao thu bua thang nay truoc trc roi tuan hom qua hqua nay hnay ngay gan nhat nam trong quan tiem shop cua hang tiệm ban duoc dc đc ra tong cong total tat ca cac mat mon minh mik cho hoi check giup gium jum coi xem e em ad oi vay z zay ha nhi nha nhe day do a ak tinh theo la so hen rua nghen nhoa he nhen ta'.split())
AFTER_UNIT = r'size|tinh|tong|tat|cong|thang|tuan|hom|hqua|hnay|quy|nam|trong|roi|vay|z|zay|ha|nhi|nha|nhe|day|do|a|ak|hen|rua|nghen|nhoa|he|nhen'


SPOKEN = [(r'(\d),(\d{1,2})(?=%|(?:ml|l|m|kg|g|mg|tr|trieu)\b)', r'\1.\2'),
          (r'\bq([1-4])/(\d{4})\b', r'quy \1/\2'),
          (r'\b(?:th|thang) (?:nay|ni)\b', 'thang nay'),
          (r'(?<!led )\b(?:th|t) ?(\d{1,2})\b(?![/.\w-])(?! ?\d*\.?\d*m\b)', r'thang \1'),
          (r'\b(?:dc|duco|duowc|đc)\b', 'duoc'), (r'\bdoanhthu\b', 'doanh thu'), (r'\bnhiu\b', 'nhieu'),
          (r'\btro (?:lne|leen)\b|\btrowr leen\b', 'tro len'), (r'\bwan\b', 'quan'), (r'\bdong tien hang\b', 'don tien hang'), (r'(\d) dong\b(?! tro)(?=.*\bkhach\b|$)', r'\1 don'),
          (r'\b(ngay|hang|mon|sp|tho|thang|may|khach) (?:mo|chi)\b(?! tieu| nhieu| tien)', r'\1 nao'), (r'\bngay naof\b', 'ngay nao'), (r'\b(?:bua|hom) nao\b', 'ngay nao'), (r'(\d+) cu\b', r'\1 trieu'), (r'\bdon hang\b', 'don'), (r'\bbao nheiu\b', 'bao nhieu'), (r'\bvaf\b', 'va'), (r'\bnhieuf\b', 'nhieu'), (r'\bn(uo[tn]g?) (xe)\b', r'luot \2'), (r'\blan nuot\b', 'lan luot'), (r'\brevenue\b', 'doanh thu'), (r'\bty le\b', 'ti le'), (r'\bdthu\b', 'doanh thu'), (r'\bth (truoc|trc|roi)\b', r'thang \1'), (r'\bthang (?:truoc|trc) do\b', 'thang truoc'), (r'≥ ?', 'it nhat '), (r'\btien tuoi\b', 'tien mat'), (r'\bkhong can don bac si\b', 'khong ke don'), (r'\btru thuoc ke don\b', 'khong ke don'), (r'\border\b', 'don'), (r'\bees\b', 'e'), (r'\bnguoi ban\b', 'nhan vien'), (r'\bcac mat hang con lai\b', ' '), (r'\bnhan dien\b', 'nhan vien'), (r'\bit nhat (\d+) dong\b', r'it nhat \1 don'), (r'\bnhieu ten\b', 'nhieu tien'), (r'\blenn\b', 'len'), (r'\b(?:tru (?:het )?chi phi[^0-9]{0,25}?\b(?:lai|loi)(?: rong| that)?|(?:lai|loi) that|\bloi nhuan rong)\b', 'lai rong'), (r'\bthang thang\b', 'thang'), (r'\btuan trc\b', 'tuan truoc'), (r'\b(?:bua|hom) ?qua\b|\bhqua\b', 'hom qua'), (r'\b(?:bua nay|bua ni|hom ni|hnay)\b', 'hom nay'), (r'\btuan ni\b', 'tuan nay'), (r'\bnam ni\b', 'nam nay'), (r'\b(?:loi|lai) nhuan gop\b|\bloi gop\b', 'lai gop'),
          (r'\b(?:toi bay chu|toi chu|den chu|toi gio|den gio|cho toi nay|tinh toi nay)\b', 'den het hom nay'),
          (r'\bdao(?= may gio\b)', 'vao'), (r'\bkua\b', 'cua'), (r'\bbna\b', 'ban'), (r'\b(?:hagn|hangf)\b', 'hang'), (r'\bnhta\b', 'nhat'),
          (r'\btieng bang\b', 'tien ban'), (r'\btieng\b(?! anh| viet| hoa| trung| han| nhat| dong ho)', 'tien'),
          (r'\bbin quan\b', 'binh quan'), (r'\btrung bih\b', 'trung binh'), (r'\bda tri\b', 'gia tri'),
          (r'\bchong(?= (?:quy|thang|nam|tuan|kho|\d|it nhat)\b)', 'trong'),
          (r'\bchen(?= (?:moi|1|mot) (?:don|dh|bill)\b)', 'tren'),
          (r'\b(tuan|thang|nam|hom|bua|don|dh|tien|xong) goi\b', r'\1 roi'), (r'\brooif\b', 'roi'),
          (r'\bo do\b', 'don'), (r'\bbest ?seller\b', 'mat hang ban chay nhat'),
          (r'\btop nam(?= (?:mon|mat hang|san pham|sp|khach|dong)\b)', 'top 5'),
          (r'\bthang ba\b(?!o)', 'thang 3'), (r'\b(?:dzay|za)\b', 'vay')]
GENERIC_TOTAL = set(('cho toi biet em hoi xin nho xem coi check jum gium giup minh mik shop quan tiem cua hang sop ban duoc ra chot tong cong tat ca ca la bao nhieu bnhiu bn may mi nhieu '
                     'roi vay z zay ha nhi nha nhe day do a ak the nay ne rua ru hen nghen nhoa he nhen hi ke len dat tien don dh bill o doanh thu dthu so co dem trong tu dau den het toi rieng ngay nay tinh luon hom cua total nam biet ve e ee ta ha toi cko ko da mua thi').split())


# Words typed with Vietnamese key strokes left in, like soos, thangs, ddown, toongr.
TYPED = set('so don hom thang tuan nam ngay tien ban khach hang nhom tong dem gia het no nhieu lai gop mat mon doanh thu mot vao tra trong duoc dot xe la bao tram toi cho nha tro dien thoai tinh cua cong gan vay nhat huy biet moi gium giup sang len roi da quy nhap ton chay tang giam luot phieu nhan vien binh quan nay do may co tieu nao bot con hon cung it'.split())


def untyped(word):
    """The plain word behind stray key strokes; a word that is not one of the known ones stays as written."""
    if word in TYPED:
        return word
    plain = re.sub(r'aa|aw', 'a', re.sub(r'ee', 'e', re.sub(r'oo|ow', 'o', re.sub(r'uw', 'u', re.sub(r'^dd', 'd', word)))))
    for candidate in (plain, plain[:-1] if plain[-1:] in set('sfrxj') else plain):
        if candidate in TYPED:
            return candidate
    return word


# Names the rulebook counts as one thing; a shop's catalogue uses one of each pair.
SAME_NAMES = [('ran', 'chien'), ('ma lon', 'ma heo'), ('thit lon', 'thit heo'), ('dau phu', 'dau hu'), ('lac', 'dau phong'), ('hoa qua', 'trai cay'), ('coc sac', 'cu sac'),
              ('ca trau', 'ca loc'), ('ke dien thoai', 'gia do dien thoai'), ('pho mat', 'pho mai'), ('xay trat', 'xay to'), ('ngao', 'ngheu'), ('bo cuoi', 'con bo cuoi')]


def spoken(plain):
    """Rewrite common spoken forms and typos into the forms the rules read."""
    plain = re.sub(r'[a-z]+', lambda found: untyped(found[0]), plain)
    for pattern, replacement in SPOKEN:
        plain = re.sub(pattern, replacement, plain)
    return plain


def words_of(plain):
    return re.findall(r'[\w.%/+-]+', plain)


def named_months(plain):
    return [(int(m), int(y)) for m, y in re.findall(r'\b(?:thang|th) (\d{1,2})(?:/| nam )(\d{4})', plain)]


def one_month(plain, as_of):
    months = set(named_months(plain))
    if len(months) == 1:
        (month, year), = months
        return month_range(year, month)
    if not months and re.search(r'\bthang nay\b', plain):
        return resolve_period({'kind': 'this_month'}, as_of)
    if not months and re.search(r'\bthang (?:truoc|trc|roi)\b', plain):
        return resolve_period({'kind': 'last_month'}, as_of)
    bare = set(re.findall(r'\bthang (\d{1,2})\b(?!/)', plain))
    if not months and len(bare) == 1 and 1 <= int(next(iter(bare))) <= 12:
        return resolve_period({'kind': 'month', 'month': int(next(iter(bare)))}, as_of)
    return None


def any_period(plain, as_of):
    """The one period the question names, cut at today since later records have not happened; None when unclear."""
    if re.search(r'\b(?:tu )?(?:dau nam|(?:ngay )?1/1)\b', plain) and re.search(r'\bhet (?:hom nay|hnay|bua nay)\b', plain):
        start, end = resolve_period({'kind': 'year_to_date'}, as_of)
        return start, min(end, date.fromisoformat(as_of))
    # Whole months written as a range.
    whole = re.search(r'\bthang (\d{1,2})/(\d{4}) (?:den|toi) (?:het )?thang (\d{1,2})/(\d{4})\b', plain)
    if whole:
        start = month_range(int(whole[2]), int(whole[1]))[0]
        return start, min(month_range(int(whole[4]), int(whole[3]))[1], date.fromisoformat(as_of))
    if re.search(r'\btu (?:ngay )?\d+/\d+|den het|toi het|\bket thuc\b', plain):
        return None
    last = re.search(r'(\d+) ngay (?:gan nhat|gan day|qua|vua qua)', plain)
    if last:
        period = {'kind': 'last_days', 'days': int(last[1])}
    else:
        span = one_month(plain, as_of)
        if span is not None:
            return span[0], min(span[1], date.fromisoformat(as_of))
        slots = {}
        try:
            fix_period(slots, plain)
        except Unsupported:
            return None
        period = slots.get('period')
        if period is None:
            return None
    start, end = resolve_period(period, as_of)
    return start, min(end, date.fromisoformat(as_of))


def debt_day(plain, as_of):
    """The day a balance is read at: one written date, or today when the question says so."""
    days = re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain)
    if len(days) == 1:
        return date(int(days[0][2]), int(days[0][1]), int(days[0][0]))
    return date.fromisoformat(as_of) if not days and re.search(r'hom nay|hnay|bua nay', plain) else None


def at_least(plain):
    """The amount in "tu 3tr7 tro len" or "it nhat 10.000.000 dong"; None when absent."""
    number = r'(\d+(?:[.,]\d+)*) ?(trieu|tr|nghin|ngan|k)? ?(\d)?(?: ?dong| ?d)?'
    found = re.search(number + r' tro len', plain) or re.search(r'it nhat ' + number + r'(?! ?don)', plain)
    if not found:
        return None
    digits = found[1]
    if re.fullmatch(r'\d{1,3}(?:[.,]\d{3})+', digits):
        value = float(re.sub(r'[.,]', '', digits))
    else:
        value = float(digits.replace(',', '.'))
    scale = {'trieu': 1000000, 'tr': 1000000, 'nghin': 1000, 'ngan': 1000, 'k': 1000}.get(found[2], 1)
    if found[3]:
        value += int(found[3]) / 10 if found[2] else 0
    return value * scale


class Direct:
    def __init__(self, metrics):
        self.metrics = metrics
        # The model's form reaches the rules' raw-column readers through this.
        metrics.direct = self
        self.books = None
        dec = lambda s: re.sub(r'(\d),(\d{1,2})(?=%|(?:ml|l|m|kg|g|mg|tr|trieu)\b)', r'\1.\2', s)
        self.products = [(row[0], row[1], row[2], words_of(dec(name_key(row[1], accents=True))))
                         for row in metrics.rows('SELECT product_id, product_name, base_unit FROM products WHERE NOT COALESCE(is_service, 0)')]
        try:
            for pid, pname, bunit, alias in metrics.rows(
                'SELECT p.product_id, p.product_name, p.base_unit, a.alias FROM product_aliases a JOIN products p USING(product_id) WHERE NOT COALESCE(p.is_service, 0)'
            ):
                tokens = words_of(dec(name_key(alias or '', accents=True)))
                if tokens:
                    self.products.append((pid, pname, bunit, tokens))
        except sqlite3.Error:
            pass
        self.ingredients = {name_key(row[0], accents=True): row[0] for row in
                            metrics.rows('SELECT DISTINCT active_ingredient FROM products WHERE active_ingredient IS NOT NULL')}
        # DATAMATE_RULES=train keeps only the rules the training split backs (datamate/provenance.py).
        switch_off(self, 'direct')

    def product(self, plain):
        """The one product whose whole name is in the question, in order or not; a shared match declines."""
        words = set(words_of(plain))
        text = ' ' + ' '.join(words_of(plain)) + ' '
        exact = [p for p in self.products if p[3] and ' ' + ' '.join(p[3]) + ' ' in text]
        found = exact or [p for p in self.products if len(p[3]) > 1 and words.issuperset(p[3])]
        longest = max((len(p[3]) for p in found), default=0)
        found = [p for p in found if len(p[3]) == longest]
        return found[0] if len({p[0] for p in found}) == 1 else None

    def shop_names(self, plain):
        """Say a product the way this shop's catalogue says it."""
        names = ' ' + ' | '.join(' '.join(p[3]) for p in self.products) + ' '
        for one, other in SAME_NAMES:
            for said, kept in ((one, other), (other, one)):
                if re.search(r'\b' + said + r'\b', plain) and not re.search(r'\b' + said + r'\b', names) and re.search(r'\b' + kept + r'\b', names):
                    plain = re.sub(r'\b' + said + r'\b', kept, plain)
        return plain

    def named_product(self, plain):
        """Like product, but the name must stand whole and in order, since common words can spell a short name out of order."""
        found = self.product(plain)
        return found if found is not None and ' '.join(found[3]) in ' '.join(words_of(plain)) else None

    def without_product(self, plain):
        """The question with the matched product name taken out, since a name may hold words like vi or dien thoai."""
        product = self.product(plain)
        if product is None:
            return plain
        whole = ' '.join(product[3])
        return plain.replace(whole, ' ') if whole in plain else ' '.join(word for word in plain.split() if word not in product[3])

    def answer(self, question):
        """A final answer, or None when no rule is sure of the whole question.

        Under DATAMATE_RULES=train a question that only an unbacked rule recognises is withheld as an abstain answer."""
        try:
            return self.answer_rules(question)
        except Withheld as rule:
            return {'type': 'abstain', 'reason': f'withheld: {rule} has no training support'}

    def answer_rules(self, question):
        raw_q = re.sub(r'\bgồi\b', 'rồi', question['question'], flags=re.IGNORECASE)
        plain = standard_months(name_key(raw_q, accents=True))
        plain = self.shop_names(spoken(plain))
        # Taking accents out makes hang (goods) and hang (brand) the same word.
        self.brand_asked = 'hãng' in question['question'].lower()
        for rule in (self.opinion, self.outside_data, self.price_change, self.buy_price, self.series, self.best_day, self.best_month, self.rulebook_superlative, self.drug, self.product_units, self.product_revenue,
                     self.days_over, self.orders_over, self.customers_over, self.items_over, self.units_total, self.top_items, self.debt_age, self.top_debtors, self.customer_debt, self.debtor_count, self.plain_total, self.revenue_growth,
                     self.purchases_spoken, self.months_named, self.group_totals, self.two_values, self.top_customer, self.same_days_growth, self.unsold):
            try:
                found = rule(plain, question['as_of_date'])
            except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, sqlite3.Error):
                # Unsupported is a ValueError too; a rule that trips on one shop must not cost the model its turn.
                continue
            if found is not None:
                return found
        self.open_books()
        found = self.common.answer(plain, question['as_of_date'])
        if found is not None:
            return found
        found = self.books.answer(plain, question['as_of_date'])
        if found is None and self.ledgers is not None:
            found = self.ledgers.answer(plain, question['as_of_date'])
        return found if found is not None else self.not_recorded(plain, question['as_of_date'])

    def open_books(self):
        if self.books is None:
            from datamate.books import Books
            from datamate.common import Common
            from datamate.ledgers import Ledgers
            self.common = Common(self)
            self.books, self.ledgers = Books(self), Ledgers(self) if getattr(self.metrics.data, 'raw', None) is not None else None
            switch_off(self.common, 'common')
            switch_off(self.books, 'books')
            if self.ledgers is not None:
                switch_off(self.ledgers, 'ledgers')

    @staticmethod
    def month_list(plain, as_of):
        """Months named in a row as written: "th4, th5, th6", "thang 10, 11, 12 nam 2025", "T7, T8, T9/2025"; a range fills in its middle."""
        token = r'\d{1,2}(?:/\d{4})?'
        run = re.search(r'\bthang ' + token + r'(?:(?:, ?| va )(?:thang )?' + token + r')+(?: nam \d{4})?', plain)
        text = run[0] if run else plain
        found = [(int(m), int(y) if y else None) for m, y in re.findall((r'\b(\d{1,2})(?:/(\d{4}))?\b(?! nam)' if run else r'\bthang (\d{1,2})(?:/(\d{4}))?\b'), re.sub(r' nam \d{4}$', '', text))]
        year = re.search(r' nam (\d{4})$', text)
        if year and found:
            found[-1] = (found[-1][0], int(year[1]))
        mentions = list(re.finditer(r'\bthang \d', plain))
        inside = [m for m in mentions if run and run.start() <= m.start() < run.end()]
        if run and len(inside) != len(mentions):
            return None
        if not run and any(len(plain[x.end():y.start()].split()) > 4 for x, y in zip(mentions, mentions[1:])):
            return None
        if len(found) < 2 or any(not 1 <= m <= 12 for m, _ in found):
            return None
        if found[-1][1] is None:
            found[-1] = (found[-1][0], resolve_period({'kind': 'month', 'month': found[-1][0]}, as_of)[0].year)
        for i in range(len(found) - 2, -1, -1):
            if found[i][1] is None:
                found[i] = (found[i][0], found[i + 1][1] - (found[i][0] > found[i + 1][0]))
        if len(found) == 2 and re.search(r'\b(?:tu|trong)\b.*thang \d+/\d+ (?:den|toi|qua|sang) (?:het )?thang|thang \d+/\d+ (?:den|toi) (?:het )?thang', plain):
            low, high = found[0][1] * 12 + found[0][0] - 1, found[1][1] * 12 + found[1][0] - 1
            found = [(i % 12 + 1, i // 12) for i in range(low, high + 1)]
        return found if len(set(found)) == len(found) else None

    def months_named(self, plain, as_of):
        """Revenue month by month as a list, or whether it rose every month as a yes or no."""
        if not re.search(r'doanh thu|dthu', plain) or re.search(FILTERS, plain) or re.search(r'\bnao\b|%|phan tram|trung binh', plain):
            return None
        pairs = re.search(r'cao hon.*cao hon', plain) and sorted(set(named_months(plain)), key=lambda m: (m[1], m[0]))
        months = pairs if pairs and len(pairs) >= 3 else self.month_list(plain, as_of)
        if months is None:
            return None
        totals = [self.metrics.revenue(*month_range(y, m), None) for m, y in months]
        if re.search(r'tang lien tuc|(?:luon|deu) (?:cao )?hon|cao hon.*cao hon', plain):
            ordered = [t for _, t in sorted(zip([y * 12 + m for m, y in months], totals))]
            return {'type': 'boolean', 'answer': all(b > a for a, b in zip(ordered, ordered[1:]))}
        if re.search(r'lan luot|moi thang|tung thang|thu tu|\bcho\b|\bcko\b|nhieu\b|bnhiu', plain) and not re.search(r'\bhon\b|\bso\b', plain):
            return {'type': 'list', 'answer': [money(t) for t in totals]}
        return None

    def unit_names(self):
        rows = self.metrics.rows('SELECT DISTINCT COALESCE(p.base_unit, l.sale_unit) FROM order_lines l JOIN products p USING(product_id) WHERE COALESCE(p.base_unit, l.sale_unit) IS NOT NULL')
        return {name_key(row[0], accents=True): row[0] for row in rows}

    def group_of(self, plain):
        groups = [row[0] for row in self.metrics.rows('SELECT DISTINCT product_group FROM products WHERE product_group IS NOT NULL')]
        named = [g for g in groups if re.search(r'nhom (?:hang )?' + re.escape(name_key(g, accents=True)) + r'\b', plain)]
        return max(named, key=len) if named else None

    def group_totals(self, plain, as_of):
        """Share of revenue, orders or units of one named product group."""
        group = self.group_of(plain) if re.search(r'\bnhom\b', plain) else None
        span = any_period(plain, as_of)
        if group is None or span is None or re.search(FILTERS.replace('|nhom', ''), plain) or re.search(r'\bnao\b|\bkhach\b|tang|giam', plain.replace(name_key(group, accents=True), ' ')):
            return None
        window = [span[0].isoformat(), span[1].isoformat(), group]
        where = 'FROM sales WHERE order_date BETWEEN ? AND ? AND product_group = ?'
        if re.search(r'%|phan tram|ti trong|ti le|ty le|chiem', plain):
            whole = self.metrics.revenue(*span, None)
            return {'type': 'percent', 'answer': two(100 * (self.metrics.rows('SELECT SUM(line_revenue) ' + where, window)[0][0] or 0) / whole)} if whole else None
        if re.search(HOW_MANY + r' (?:don|dh)\b|\bso (?:don|dh)\b.*' + HOW_MANY + r'|\bdem so (?:don|dh)\b', plain):
            return {'type': 'number', 'answer': self.metrics.rows('SELECT COUNT(DISTINCT order_id) ' + where, window)[0][0]}
        units = self.unit_names()
        asked = [key for key in units if key not in UNITLESS and re.search(r'(?:' + HOW_MANY + r'|tong so) ' + re.escape(key) + r'\b', plain)]
        if len(asked) == 1:
            sold = self.metrics.rows('SELECT SUM(basic_quantity) ' + where + ' AND COALESCE(base_unit, sale_unit) = ?', window + [units[asked[0]]])[0][0]
            return {'type': 'number', 'answer': sold} if sold else None
        return None

    def opinion(self, plain, as_of):
        """Judgments of people that no record or rulebook line can settle."""
        if re.search(r'chat luong|ngon nhat|\bba bau\b|than thien|trung thuc|kho tinh nhat|uy tin|dang tin cay|nhiet tinh|tan tam|\bkhen\b|de thuong|lich su nhat|lanh nghe|tay nghe|le phep|chu dao|quy men|\bnen uong\b|\blieu dung\b', plain):
            return {'type': 'unanswerable', 'reason': 'no record measures this'}
        return None

    def outside_data(self, plain, as_of):
        """A period wholly outside the shop's dates has no data, which differs from zero (rulebook section 7)."""
        span = any_period(plain, as_of)
        if span is None or not (span[1].isoformat() < self.metrics.first or span[0].isoformat() > self.metrics.last):
            return None
        return {'type': 'unanswerable', 'reason': 'period is outside the data'}

    def _has_nonempty_col(self, pattern):
        if not hasattr(self, '_col_cache'):
            self._col_cache = {}
        if pattern not in self._col_cache:
            found = False
            if self.books and self.books.raw is not None:
                for tname, cols in self.books.columns.items():
                    matched = [c for c in cols if re.search(pattern, name_key(c, accents=True))]
                    for col in matched:
                        row = self.books.connection.execute(f'SELECT 1 FROM "{tname}" WHERE "{col}" IS NOT NULL AND TRIM(CAST("{col}" AS TEXT)) != "" LIMIT 1').fetchone()
                        if row:
                            found = True
                            break
                    if found:
                        break
            self._col_cache[pattern] = found
        return self._col_cache[pattern]

    def has_customers(self):
        return 'customer_identity' in self.metrics.capabilities or bool(self.metrics.rows('SELECT 1 FROM customers LIMIT 1'))

    def has_timestamps(self):
        return 'order_timestamps' in self.metrics.capabilities or bool(self.metrics.rows('SELECT 1 FROM orders WHERE created_at IS NOT NULL AND length(created_at) >= 13 LIMIT 1'))

    def not_recorded(self, plain, as_of=None):
        """The question turns on a kind of record this shop does not keep."""
        have = self.metrics.capabilities
        if re.search(r'\b(?:huy|tra lai|tra mon|tra hang)\b.*\bvi (?:sao|giao|goi|mon|loi|mua|khach|hang)\b', plain) and not any(re.search(r'ly do|nguyen nhan', name_key(c, accents=True)) for columns in self.books.columns.values() for c in columns):
            return {'type': 'unanswerable', 'reason': 'shop does not record reasons'}
        if re.search(r'\bnhom (?!khach|than thiet|si\b|le\b|may\b)', plain) and not self.metrics.labels()['product_group'] and not re.search(r'\bkhach\b.*\bnhom\b', plain):
            return {'type': 'unanswerable', 'reason': 'shop does not record product groups'}
        if self.books.raw is not None and 'customer_groups' not in have and re.search(r'\bnhom khach\b|\bkhach nhom\b', plain):
            return {'type': 'unanswerable', 'reason': 'shop does not record customer groups'}
        if not self.has_customers() and re.search(r'\bkhach (?:hang )?(?:nao|trung thanh|sop|hay doi y)\b|\btop \d+ khach\b', plain) and not re.search(r'dong khach|vang khach|khach phai tra', plain):
            return {'type': 'unanswerable', 'reason': 'shop does not record customers'}
        if self.ledgers is not None and not self.ledgers.has_column(r'giavon') and re.search(r'\blai gop\b|\blai rong\b|\bloi nhuan\b|\b' + ITEM + r' nao\b.*\b(?:loi|lai)\b|\b(?:loi|lai) (?:nhieu )?(?:nhat|nhut)\b', plain):
            return {'type': 'unanswerable', 'reason': 'shop does not record cost price'}
        # A shop that keeps neither a gender nor an address column (with data) cannot say who its women or its streets are (section 7).
        if self.books.raw is not None and ((re.search(r'\bkhach (?:nu|nam)\b|gioi tinh', plain) and not self._has_nonempty_col(r'gioi tinh|nam/nu|\bphai\b'))
                                           or (re.search(r'\bkhach (?:hang )?(?:o )?duong\b|\bo duong\b|\bkhach o\b', plain) and not self._has_nonempty_col(r'dia chi|\baddr\b'))):
            return {'type': 'unanswerable', 'reason': 'shop does not record this'}
        if re.search(r'\bca sang\b|\bca chieu\b|gio cao diem|dong khach nhat.*may gio|may gio.*dong khach nhat', plain) and not self.has_timestamps():
            return {'type': 'unanswerable', 'reason': 'shop does not record order timestamps'}
        for pattern, needed in ((r'qua dien thoai|tai quay|zalo|san thuong mai dien tu|san tmdt', {'sales_channels'}),
                                (r'tien mat|chuyen khoan|vi dien tu|\bcod\b', {'payment_method'}),
                                (r'tien nhap|nhap hang|gia nhap', {'purchase_prices'}),
                                (r'ton kho|trong kho|\bcon ton\b|\bkho\b.*\bton\b|han dung|\bhsd\b', {'stock_report', 'inventory_lots'}),
                                (r'\btra (?:bot )?no\b|\bthu hoi\b|\bthu no\b|\bno them\b', {'payments'}),
                                (r'\bcon no\b|\bkhoan no\b|\bcong no\b|\bdang no\b|\btuoi no\b|\bcon thieu\b|\bno chua thu\b', {'payments', 'debt_snapshot'})):
            if re.search(pattern, plain) and not have & needed and not (needed & {'payments'} and self.books.table('Góp/tháng')) and not (needed & {'stock_report'} and self.ledgers and self.ledgers.has_column(r'^toncuoi$')):
                return {'type': 'unanswerable', 'reason': 'shop does not record this'}
        if self.books.raw is not None and re.search(r'\bcuoi ngay\b.*\bcon\b', plain) and not (have & {'stock_report', 'inventory_lots'}) and not (self.ledgers and self.ledgers.has_column(r'^toncuoi$')) and self.product(plain) is not None:
            return {'type': 'unanswerable', 'reason': 'shop does not record stock'}
        if 'payments' not in have and 'debt_snapshot' in have and not self.books.table('Góp/tháng') and as_of is not None:
            if re.search(r'\bcon no\b|\bkhoan no\b|\bcong no\b|\bdang no\b|\btuoi no\b|\bcon thieu\b|\bno chua thu\b', plain):
                end = debt_day(plain, as_of)
                if end is not None and self.metrics.last and (end - date.fromisoformat(self.metrics.last)).days < 0:
                    return {'type': 'unanswerable', 'reason': 'shop only records end-of-data debt snapshot'}
        return None

    def purchases_spoken(self, plain, as_of):
        """Spoken forms of the month's purchase bill."""
        if not re.search(r'(?:cat|danh) hang|nhap hang|hang nhap|phieu nhap', plain) or not re.search(r'\btien\b|gia tri', plain) or self.product(plain) is not None or re.search(r'\bnao\b|\bban\b.*\bnhap\b|\bvon\b', plain):
            return None
        span = any_period(plain, as_of)
        return None if span is None else self.metrics.metric_purchases({}, span[0], span[1], as_of)

    def two_values(self, plain, as_of):
        """Revenue of two named channels or two named payment ways, in the order asked."""
        if not re.search(r'doanh thu|dthu', plain) or not re.search(r'lan luot|moi ben|tung ben|moi kenh|tung kenh|moi loai|\bcho (?:toi|minh)\b.*\bva\b|(?:bao nhieu|bnhiu|nhieu)\b.*\b(?:bao nhieu|bnhiu|nhieu)\b', plain):
            return None
        kinds = {'channel': ('sales_channels', {'tai quay': 'store', 'dien thoai': 'phone', 'zalo': 'zalo', 'san thuong mai dien tu': 'san', 'san tmdt': 'san'}),
                 'payment': ('payment_method', {'tien mat': 'cash', 'tm': 'cash', 'ck': 'transfer', 'chuyen khoan': 'transfer', 'vi dien tu': 'ewallet', 'cod': 'cod', 'xi o di': 'cod'})}
        for slot, (capability, words) in kinds.items():
            named = sorted((found.start(), label) for word, label in words.items() for found in re.finditer(r'\b' + word + r'\b', plain))
            labels = list(dict.fromkeys(label for _, label in named))
            if len(labels) != 2:
                continue
            if capability not in self.metrics.capabilities:
                return {'type': 'unanswerable', 'reason': 'shop does not record this'}
            span = any_period(plain, as_of)
            # Shops spell channels differently, so each asked channel is matched to this shop's own label.
            hints = {'store': r'store|tai ', 'phone': r'phone|dien thoai', 'zalo': r'zalo', 'san': r'\bsan\b|tmdt|marketplace'}
            known = self.metrics.labels()[slot]
            found = [label if slot == 'payment' else next((k for k in known if re.search(hints[label], name_key(k, accents=True))), None) for label in labels]
            if span is None or None in found:
                return None
            return {'type': 'list', 'answer': [money(self.metrics.revenue(*span, {slot: label})) for label in found]}
        return None

    def top_customer(self, plain, as_of):
        by_orders, by_money = re.search(r'nhieu ?(?:don|dh)\b|so don (?:mua )?nhieu|nhieu lan', plain), re.search(r'nhieu tien|tong tien mua hang cao|chi tieu nhieu', plain)
        if bool(by_orders) == bool(by_money) or not re.search(r'nhat|nhut', plain) or not re.search(r'khach (?:nao|hang nao)|\bai\b', plain) or re.search(r'\bno\b|duong|nhom|nhan vien|tho\b|\btop\b|(?<![/\d-])\b\d+ (?:khach|kh|nguoi)\b', plain):
            return None
        span = any_period(plain, as_of)
        if span is None:
            return None
        if not self.has_customers():
            return {'type': 'unanswerable', 'reason': 'shop does not record customers'}
        measure = 'COUNT(DISTINCT order_id)' if by_orders else 'SUM(line_revenue)'
        rows = self.metrics.rows(f'SELECT MIN(customer_name), {measure} FROM sales WHERE customer_id IS NOT NULL AND order_date BETWEEN ? AND ? GROUP BY customer_id ORDER BY 2 DESC',
                                 [span[0].isoformat(), span[1].isoformat()])
        if not rows or (len(rows) > 1 and rows[0][1] == rows[1][1]):
            return None
        return {'type': 'string', 'answer': rows[0][0]}

    def same_days_growth(self, plain, as_of):
        """This month so far against the same days of last month."""
        explicit_same = re.search(r'cung ky thang (?:truoc|trc|roi)|cung so ngay (?:dau )?(?:cua )?(?:thang|th) (?:truoc|trc|roi)', plain)
        implicit_mtd = re.search(r'\bthang nay\b.*\bso (?:voi |vs )?thang (?:truoc|trc|roi)\b.*(?:%|phan tram)', plain) and not re.search(r'\bthang \d', plain)
        if not (explicit_same or implicit_mtd) or not re.search(r'doanh thu|dthu|tien ban|tien hang', plain) or re.search(FILTERS, plain):
            return None
        today = date.fromisoformat(as_of)
        last = date(today.year - (today.month == 1), today.month - 1 or 12, 1)
        last_end = date(last.year, last.month, min(today.day, month_range(last.year, last.month)[1].day))
        if last_end.isoformat() < self.metrics.first or last.isoformat() > self.metrics.last:
            return {'type': 'unanswerable', 'reason': 'comparison period is outside the data'}
        before = self.metrics.revenue(last, last_end, None)
        return {'type': 'percent', 'answer': two(100 * (self.metrics.revenue(today.replace(day=1), today, None) - before) / before)} if before else None

    def unsold(self, plain, as_of):
        """Items sold before the period with no sale inside it (rulebook section 5)."""
        if not re.search(ITEM + r' (?:bi )?e\b|\bbi e\b|\b(?:khong|ko|k) ban (?:duoc|dc|ra)|hok ban|khong ai mua|ban cham den muc', plain) or not re.search(HOW_MANY + r' (?:' + ITEM + r')|' + ITEM + r' nao|\bso ' + ITEM + r'\b', plain) or re.search(FILTERS, plain):
            return None
        days = re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain)
        if len(days) == 2 and re.search(r'\btu\b', plain):
            span = tuple(date(int(y), int(m), int(d)) for d, m, y in days)
        else:
            span = any_period(plain, as_of)
        if span is None:
            return None
        rows = self.metrics.rows('SELECT DISTINCT product_name FROM sales WHERE order_date < ? AND product_id NOT IN (SELECT product_id FROM sales WHERE order_date BETWEEN ? AND ?) ORDER BY 1',
                                 [span[0].isoformat(), span[0].isoformat(), span[1].isoformat()])
        return {'type': 'number', 'answer': len(rows)} if re.search(HOW_MANY + r'|\bdem\b|\bso ' + ITEM + r'\b', plain) else None

    def quantity(self, product_id, start, end):
        return self.metrics.rows('SELECT SUM(basic_quantity) FROM sales WHERE product_id = ? AND order_date BETWEEN ? AND ?',
                                 [product_id, start, end])[0][0] or 0

    def price_change(self, plain, as_of):
        from datetime import date, timedelta
        if not re.search(r'tang gia|len gia|doi gia|\blen \d', plain):
            return None
        days = set(re.findall(r'(\d+) ngay (?:ke tu|ngay truoc|truoc|dau|tu|sau|lien truoc)', plain))
        dates = set(re.findall(r'(\d{1,2})/(\d{1,2})/(\d{4})', plain))
        product = self.product(plain)
        if len(days) != 1 or len(dates) > 1 or product is None:
            return None
        count = int(next(iter(days)))
        if dates:
            d, m, y = next(iter(dates))
            first = date(int(y), int(m), int(d))
        else:
            self.open_books()
            first = self.ledgers.raise_day(product, plain) if self.ledgers else None
            if first is None:
                return None
            first = date.fromisoformat(str(first)[:10])
        after = self.quantity(product[0], first.isoformat(), (first + timedelta(count - 1)).isoformat())
        before = self.quantity(product[0], (first - timedelta(count)).isoformat(), (first - timedelta(1)).isoformat())
        if not before:
            return None
        return {'type': 'percent', 'answer': two(100 * (after - before) / before)}

    def buy_price(self, plain, as_of):
        if not re.search(r'nhap', plain) or not re.search(r'binh quan', plain):
            return None
        span, product = one_month(plain, as_of), self.product(plain)
        unit = re.search(r'\b(?:mot|1) (xe \d+m3|\w+)', plain)
        if span is None or product is None or unit is None:
            return None
        return self.metrics.metric_buy_price({'filters': {'product': product[1]}, 'unit': unit[1]}, span[0], span[1], as_of)

    def series(self, plain, as_of):
        if not re.search(r'doanh thu|dthu', plain) or re.search(CONDITIONS, plain):
            return None
        if not re.search(r'moi thang|tung thang|lan luot|lien nhau|lien tiep', plain):
            return None
        months = named_months(plain)
        back = re.search(r'(\d+) thang (?:lien nhau|lien tiep) (?:ket thuc|tinh toi|tinh den|den)', plain)
        between = re.search(r'tu thang \d+/\d+ (?:den|toi|qua) thang', plain)
        if back and len(months) == 1:
            index = months[0][1] * 12 + months[0][0] - 1
            months = [((i % 12) + 1, i // 12) for i in range(index - int(back[1]) + 1, index + 1)]
        elif between and len(months) == 2:
            low, high = (months[0][1] * 12 + months[0][0] - 1, months[1][1] * 12 + months[1][0] - 1)
            months = [((i % 12) + 1, i // 12) for i in range(low, high + 1)]
        elif len(months) < 2:
            return None
        return {'type': 'list', 'answer': [money(self.metrics.revenue(*month_range(y, m), None)) for m, y in months]}

    def best_day(self, plain, as_of):
        if not re.search(r'\bngay ?nao\b', plain) or re.search(CONDITIONS, plain):
            return None
        if not re.search(r'(doanh thu|dthu|nhi?e?u tien|ban dat).*(cao nh|nhat|nhut)|(cao nhat|nhat|nhut).*(doanh thu|dthu)', plain):
            return None
        if re.search(r'thap nhat|it nhat', plain):
            return None
        span = one_month(plain, as_of)
        if span is None:
            return None
        rows = self.metrics.rows('SELECT order_date, revenue FROM daily_revenue WHERE order_date BETWEEN ? AND ? ORDER BY revenue DESC, order_date',
                                 [span[0].isoformat(), span[1].isoformat()])
        if not rows or (len(rows) > 1 and rows[0][1] == rows[1][1]):
            return None
        return {'type': 'date', 'answer': rows[0][0]}

    def best_month(self, plain, as_of):
        months = set(named_months(plain))
        if len(months) < 2 and re.search(r'\bthang nao\b', plain):
            months = set(self.month_list(plain, as_of) or months)
        if not re.search(r'\bthang nao\b', plain) or len(months) < 2 or re.search(FILTERS, plain):
            return None
        if len(months) == 2 and re.search(r'thang \d+/\d+ (?:den|toi|qua|sang) (?:het )?thang', plain):
            # A range names only its two ends.
            (m1, y1), (m2, y2) = named_months(plain)[:2]
            months = {(i % 12 + 1, i // 12) for i in range(y1 * 12 + m1 - 1, y2 * 12 + m2)}
        if not re.search(r'(?:doanh thu|dthu|nhi?e?u tien).*(?:cao nhat|nhat)|cao nhat.*(?:doanh thu|dthu)', plain) or re.search(r'thap nhat', plain):
            return None
        totals = sorted(((self.metrics.revenue(*month_range(y, m), None), f'{y}-{m:02}') for m, y in months), reverse=True)
        return None if totals[0][0] == totals[1][0] else {'type': 'date', 'answer': totals[0][1]}

    def top_one(self, sql, span):
        """The first column of the single best row; a tie declines."""
        rows = self.metrics.rows(sql, [span[0].isoformat(), span[1].isoformat()])
        if not rows or (len(rows) > 1 and rows[0][1] == rows[1][1]):
            return None
        return rows[0][0]

    def rulebook_superlative(self, plain, as_of):
        """Superlatives the rulebook defines by name."""
        peak_pat = r'gio cao diem|dong khach nhat.*may gio|may gio.*dong khach nhat'
        span = any_period(plain, as_of)
        if span is None and re.search(r'\bthuong\b', plain) and re.search(peak_pat, plain) and not re.search(r'\b(?:thang|quy|nam|tuan|ngay|\d{4})\b', plain):
            if self.metrics.first and self.metrics.last:
                span = (date.fromisoformat(self.metrics.first), min(date.fromisoformat(self.metrics.last), date.fromisoformat(as_of)))
        if span is None or re.search(FILTERS, plain):
            return None
        between = 'BETWEEN ? AND ?'
        customer = "FROM sales s JOIN orders o USING(order_id) WHERE s.customer_id IS NOT NULL AND s.order_date " + between
        kinds = [
            (r'ua chuong nhat', 'string', 's.product_name', "SELECT s.product_name, COUNT(DISTINCT s.order_id) FROM sales s WHERE NOT COALESCE(s.is_service, 0) "
             f"AND s.order_date {between} GROUP BY s.product_id ORDER BY 2 DESC"),
            (r'trung thanh nhat', 'string', 'c', f"SELECT MIN(s.customer_name), COUNT(DISTINCT s.order_date) {customer} GROUP BY s.customer_id ORDER BY 2 DESC"),
            (r'\bsop nhat', 'string', 'c', f"SELECT MIN(s.customer_name), SUM(s.line_revenue) / COUNT(DISTINCT s.order_id) {customer} "
             "GROUP BY s.customer_id HAVING COUNT(DISTINCT s.order_id) >= 3 ORDER BY 2 DESC"),
            (r'doi y nhat', 'string', 'c', "SELECT c.customer_name, COUNT(*) FROM orders o JOIN customers c USING(customer_id) "
             f"WHERE o.status = 'cancelled' AND o.order_date {between} GROUP BY o.customer_id ORDER BY 2 DESC"),
            (r'dat khach nhat', 'date', 'd', f"SELECT order_date, order_count FROM daily_revenue WHERE order_date {between} ORDER BY 2 DESC"),
            (peak_pat, 'number', 'h', "SELECT CAST(substr(o.created_at, 12, 2) AS INTEGER), COUNT(DISTINCT s.order_id) FROM sales s JOIN orders o USING(order_id) "
             f"WHERE o.created_at IS NOT NULL AND length(o.created_at) >= 13 AND s.order_date {between} GROUP BY 1 ORDER BY 2 DESC"),
        ]
        for pattern, kind, tag, sql in kinds:
            if re.search(pattern, plain):
                if tag == 'c' and not self.has_customers():
                    return {'type': 'unanswerable', 'reason': 'shop does not record customers'}
                best = self.top_one(sql, span)
                return None if best is None else {'type': kind, 'answer': best}
        return None

    def drug(self, plain, as_of):
        ingredient = re.search(r'hoat chat', plain)
        prescription = re.search(r'ke don', plain)
        others = re.search(r'\b(?:khong|ko|k|hok|hong|kg) ke don', plain)
        if not (ingredient or prescription) or re.search(CONDITIONS.replace('|nhom', ''), plain):
            return None
        span = one_month(plain, as_of)
        if span is None or not re.search(r'doanh thu|dthu|tien|\bban\b', plain):
            return None
        if ingredient:
            names = [key for key in self.ingredients if re.search(r'hoat chat ' + re.escape(key) + r'(?:\s|$|[?.,])', plain)]
            # A mixed drug's name holds the names of its parts; it counts as one kind (rulebook).
            names = [key for key in names if not any(key != other and key in other for other in names)]
            if len(names) != 1 or re.search(r'%|phan tram', plain):
                return None
            where, params = 'p.active_ingredient = ?', [self.ingredients[names[0]]]
        else:
            where, params = 'COALESCE(p.prescription, 0) = 0' if others else 'p.prescription = 1', []
        total = self.metrics.rows(f'SELECT SUM(s.line_revenue) FROM sales s JOIN products p USING(product_id) WHERE {where} AND s.order_date BETWEEN ? AND ?',
                                  params + [span[0].isoformat(), span[1].isoformat()])[0][0] or 0
        if re.search(r'%|phan tram|ti le|ty le|chiem', plain):
            whole = self.metrics.revenue(*span, None)
            return {'type': 'percent', 'answer': two(100 * total / whole)} if whole else None
        return {'type': 'number', 'answer': money(total)}

    def product_units(self, plain, as_of):
        bare = self.without_product(plain)
        if re.search(CONDITIONS, bare) or re.search(r'nhap|\bton\b|\bkho\b|cuoi ngay|\bcon\b', bare) or not re.search(r'ban|so ', bare):
            return None
        product, span = self.product(plain), any_period(plain, as_of)
        if product is None or span is None:
            return None
        # A catalogue with no base unit names it in the product: "Ong PVC ... cay 4m".
        asked = [word for word in product[3] if word in self.unit_names() and re.search(r'(?:' + HOW_MANY + r'|\bso) ' + word + r'\b', plain)]
        written = product[2] or (asked[0] if asked else None) or self.only_sale_unit(product[0])
        if not written:
            return None
        base = name_key(written, accents=True)
        rest = ' ' + plain.replace(' '.join(product[3]), ' ') + ' '
        spelled = r'(?:may|bao nhieu|bao nhiu|\bbn|bnhiu|nhieu|so) ' + re.escape(base) + r'\b(?! tram)'
        if (base in UNITLESS and base != 'phan') or not (re.search(spelled, rest) or re.search(spelled, plain)):
            return None
        sold = self.quantity(product[0], span[0].isoformat(), span[1].isoformat())
        return {'type': 'number', 'answer': sold} if sold else None

    def only_sale_unit(self, product_id):
        """The unit a product is always sold in, for shops whose catalogue has no base unit."""
        found = self.metrics.rows('SELECT DISTINCT sale_unit FROM order_lines WHERE product_id = ? AND sale_unit IS NOT NULL', [product_id])
        return found[0][0] if len(found) == 1 else None

    def product_revenue(self, plain, as_of):
        bare = self.without_product(plain)
        if re.search(CONDITIONS, bare) or not re.search(r'doanh thu|dthu|tien', bare) or re.search(r'nhap|%|phan tram|don gia|gia ', bare):
            return None
        product, span = self.product(plain), any_period(plain, as_of)
        if product is None or span is None:
            return None
        total = self.metrics.revenue(*span, {'product': product[1]})
        return {'type': 'number', 'answer': money(total)} if total else None

    def days_over(self, plain, as_of):
        limit, span = at_least(plain), any_period(plain, as_of)
        if limit is None or span is None or re.search(FILTERS, plain) or not re.search(r'(?:' + HOW_MANY + r'|\bnhieu) (?:ngay|bua)|\bso (?:ngay|bua)\b|dem .*\b(?:ngay|bua)\b', plain):
            return None
        if not re.search(r'doanh thu|dthu|ban duoc|ban ra|nhi?e?u tien', plain):
            return None
        count = self.metrics.rows('SELECT COUNT(*) FROM daily_revenue WHERE order_date BETWEEN ? AND ? AND revenue >= ?',
                                  [span[0].isoformat(), span[1].isoformat(), limit])[0][0]
        return {'type': 'number', 'answer': count}

    def orders_over(self, plain, as_of):
        limit, span = at_least(plain), any_period(plain, as_of)
        if limit is None or span is None or re.search(FILTERS, plain) or not re.search(HOW_MANY + r' (?:don|dh|bill|hoa don)\b|\bso (?:don|dh)\b', plain):
            return None
        if not re.search(SPENT + r'|gia tri', plain):
            return None
        count = self.metrics.rows('SELECT COUNT(*) FROM (SELECT order_id FROM sales WHERE order_date BETWEEN ? AND ? GROUP BY order_id HAVING SUM(line_revenue) >= ?)',
                                  [span[0].isoformat(), span[1].isoformat(), limit])[0][0]
        return {'type': 'number', 'answer': count}

    def customers_over(self, plain, as_of):
        found = re.search(r'\bkhach\b.*?(?:tu |it nhat )?(\d+) (?:don|dh)\b', re.sub(r'\bthang \d+(?:/\d+)?(?: nam \d+)?', ' ', plain))
        span = any_period(plain, as_of)
        if not found or span is None or not re.search(r'tro len|it nhat', plain) or re.search(FILTERS.replace('|nhom', ''), plain):
            return None
        if not self.has_customers():
            return {'type': 'unanswerable', 'reason': 'shop does not record customers'}
        counts = [row[0] for row in self.metrics.rows('SELECT COUNT(DISTINCT order_id) FROM sales WHERE customer_id IS NOT NULL AND order_date BETWEEN ? AND ? GROUP BY customer_id',
                                                      [span[0].isoformat(), span[1].isoformat()])]
        many = sum(count >= int(found[1]) for count in counts)
        if re.search(r'%|phan tram|ti le|ty le|chiem', plain):
            return {'type': 'percent', 'answer': two(100 * many / len(counts))} if counts else None
        return {'type': 'number', 'answer': many} if re.search(HOW_MANY + r' (?:khach|nguoi)|nhieu khach|\bso khach\b|dem .*khach', plain) else None

    def items_over(self, plain, as_of):
        found = re.search(r'(\d+) (?:don|dh|bill|hoa don)\b', plain)
        span = any_period(plain, as_of)
        if not found or span is None or not re.search(r'tro len|it nhat', plain) or re.search(FILTERS, plain) or not re.search(HOW_MANY + ' ' + ITEM + r'\b|\bso ' + ITEM + r'\b', plain):
            return None
        count = self.metrics.rows('SELECT COUNT(*) FROM (SELECT product_id FROM sales WHERE NOT COALESCE(is_service, 0) AND order_date BETWEEN ? AND ? '
                                  'GROUP BY product_id HAVING COUNT(DISTINCT order_id) >= ?)', [span[0].isoformat(), span[1].isoformat(), int(found[1])])[0][0]
        return {'type': 'number', 'answer': count}

    def units_total(self, plain, as_of):
        asked, span = self.units_asked(plain), any_period(plain, as_of)
        return None if asked is None or span is None else self.units_sold(*asked, span)

    def units_asked(self, plain, strict=True):
        """The unit and size of a shop-wide units question, or None when the question may also name a product."""
        size = re.search(r'\bsize ([sml])\b', plain)
        if re.search(CONDITIONS.replace('|size', ''), plain) or re.search(r'nhap|\bton\b', plain) or self.product(plain) is not None:
            return None
        units = self.unit_names()
        asked = [key for key in units if key not in UNITLESS and re.search(r'(?:' + HOW_MANY + r'|nhieu|\bso|tong cong) ' + re.escape(key) + r'(?:\W*$|\W+(?:' + AFTER_UNIT + r'|ban|la)\b)', plain)]
        if len(asked) != 1 or not re.search(r'\bban\b|\bbn\b', plain):
            return None
        # Any other word may be a product we could not match, which would make a shop-wide total wrong.
        rest = re.sub(r'thang \d+/\d+|thang \d+|\d+ ngay|size [sml]|\bquy \d(?:/\d+)?|' + HOW_MANY + r'|\bnhieu\b|' + re.escape(asked[0]), ' ', plain)
        extra = set(words_of(rest)) - GENERIC
        # The model has already said no product is named, so only a word of this shop's own item names is a doubt.
        if extra if strict else extra & self.name_words():
            return None
        return units[asked[0]], size[1] if size else None

    def name_words(self):
        names = self.metrics.rows('SELECT product_name FROM products UNION SELECT product_group FROM products')
        return {word for name, in names if name for word in words_of(name_key(name, accents=True))} - GENERIC

    def units_sold(self, unit, size, span):
        """Base units sold across items whose base unit is the one asked (rulebook), of one size when named."""
        where = ' AND s.product_name LIKE ?' if size else ''
        params = [unit, span[0].isoformat(), span[1].isoformat()] + (['%(' + size.upper() + ')%'] if size else [])
        sold = self.metrics.rows('SELECT SUM(s.basic_quantity) FROM sales s JOIN products p USING(product_id) WHERE COALESCE(p.base_unit, s.sale_unit) = ? '
                                 'AND s.order_date BETWEEN ? AND ?' + where, params)[0][0]
        return {'type': 'number', 'answer': sold} if sold else None

    def top_items(self, plain, as_of):
        """Best items by the rulebook's quantity or by revenue, optionally inside one named group."""
        if getattr(self, 'brand_asked', False) or re.search(r'\bdong may\b|\bla ai\b|\bnguoi (?:mua|ban)\b|\bkh\b', plain):
            return None
        group = self.group_of(plain) if re.search(r'\bnhom\b', plain) else None
        bare = plain.replace(name_key(group, accents=True), ' ') if group else plain
        if re.search(r'it nhat|thap nhat|cham nhat', plain) or re.search(CONDITIONS.replace('|nhom', ''), bare):
            return None
        count = re.search(r'(?:top |(?<![/\w-]))(\d{1,2}) ' + ITEM + r'\b', plain) or re.search(r'\btop (\d+)', plain)
        if re.search(r'\btop\b', plain) and not count:
            return None
        if not re.search(r'nhat|nhut', plain) or not (count or re.search(r'(?:mat hang|san pham|sp|mon|hang) nao|\b(?:mat hang|san pham|sp|mon)\b.*\bla (?:gi|chi)\b', plain)) or not re.search(r'\bban\b|tien|doanh thu|dthu|\bhot\b|\bchay\b', plain) or re.search(r'\b(?:ngay|thang|tuan|may) nao\b', plain):
            return None
        span = any_period(plain, as_of)
        if span is None or re.search(r'\bnhap\b', plain):
            return None
        where, params = '', []
        if re.search(r'\bnhom\b', plain):
            groups = [row[0] for row in self.metrics.rows('SELECT DISTINCT product_group FROM products WHERE product_group IS NOT NULL')]
            named = [g for g in groups if re.search(r'nhom ' + re.escape(name_key(g, accents=True)) + r'\b', plain)]
            if len(named) != 1:
                return None
            where, params = ' AND product_group = ?', named
        units = self.unit_names()
        unit = re.search(r'nhi?e?u (\w+) nh[au]t', plain)
        if unit and unit[1] in units:
            where += ' AND COALESCE(base_unit, sale_unit) = ?'
            params = params + [units[unit[1]]]
        measure = 'SUM(line_revenue)' if re.search(r'nhi?e?u tien|doanh thu|dthu', plain) else 'SUM(basic_quantity)'
        rows = self.metrics.rows(f'SELECT MIN(product_name), {measure} FROM sales WHERE NOT COALESCE(is_service, 0) AND order_date BETWEEN ? AND ?{where} '
                                 'GROUP BY product_id ORDER BY 2 DESC, product_id', [span[0].isoformat(), span[1].isoformat()] + params)
        if count:
            size = int(count[1])
            if len(rows) < size or (len(rows) > size and rows[size - 1][1] == rows[size][1]):
                return None
            return {'type': 'list', 'answer': [row[0] for row in rows[:size]]}
        if not rows or (len(rows) > 1 and rows[0][1] == rows[1][1]):
            return None
        return {'type': 'string', 'answer': rows[0][0]}

    def owed(self, select, end, tail=''):
        """Rulebook section 4 per order at the end of a day: not cancelled, not COD, not marketplace, returned orders kept."""
        return self.metrics.unpaid(select, end, tail)

    def debt_age(self, plain, as_of):
        end = debt_day(plain, as_of)
        if end is None or not re.search(r'\bno\b|cong no', plain):
            return None
        return self.debt_by_age(plain, end)

    def debt_by_age(self, plain, end):
        """Debt owed at the end of a day by age from the order day (rulebook): the three bands, or orders older than N days."""
        age = f"(julianday('{end.isoformat()}') - julianday(order_date))"
        if re.search(r'tuoi no', plain) and re.search(r'0\D{1,3}7', plain) and re.search(r'8\D{1,3}15', plain):
            row = self.owed(f'SUM(CASE WHEN {age} <= 7 THEN owed ELSE 0 END), SUM(CASE WHEN {age} BETWEEN 8 AND 15 THEN owed ELSE 0 END), SUM(CASE WHEN {age} > 15 THEN owed ELSE 0 END)', end)[0]
            return {'type': 'list', 'answer': [money(value or 0) for value in row]}
        over = re.search(r'\b(?:qua|hon|tren) (\d+) ngay', plain)
        if over and not re.search(r'lan luot|moi nhom|chia theo', plain):
            return {'type': 'number', 'answer': money(self.owed(f'SUM(CASE WHEN {age} > {int(over[1])} THEN owed ELSE 0 END)', end)[0][0] or 0)}
        return None

    def top_debtors(self, plain, as_of):
        count = re.search(r'(?:top )?(\d+) (?:khach|kh)\b', plain) or re.search(r'\btop (\d+)', plain)
        end = debt_day(plain, as_of)
        if not count or end is None or not re.search(r'\bno\b|cong no', plain) or not re.search(r'nhi?e?u|lon nhat|nhat', plain):
            return None
        rows = self.owed('customer_id, SUM(owed)', end, 'AND customer_id IS NOT NULL GROUP BY customer_id ORDER BY 2 DESC, customer_id')
        size = int(count[1])
        if len(rows) < size or (len(rows) > size and rows[size - 1][1] == rows[size][1]):
            return None
        names = dict(self.metrics.rows('SELECT customer_id, customer_name FROM customers WHERE customer_id IN (' + ','.join('?' * size) + ')', [row[0] for row in rows[:size]]))
        return {'type': 'table', 'answer': {'columns': ['khach_hang', 'so_tien_no'], 'rows': [[names[row[0]], money(row[1])] for row in rows[:size]]}}

    def customer_debt(self, plain, as_of):
        if not re.search(r'(?:no|ghi no|mua chiu) them', plain) or not re.search(r'\btra\b', plain):
            return None
        self.metrics.need('payments')
        span = any_period(plain, as_of)
        if span is None:
            return None
        people = [row for row in self.metrics.rows('SELECT customer_id, customer_name FROM customers') if name_key(row[1] or '', accents=True) in plain]
        if len(people) != 1:
            return None
        window = [span[0].isoformat(), span[1].isoformat()]
        added = self.metrics.rows("""SELECT SUM(o.invoice_total - COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.order_id = o.order_id AND p.payment_date <= o.order_date), 0))
            FROM orders o WHERE o.customer_id = ? AND o.payment_method = 'debt' AND o.status != 'cancelled' AND o.order_date BETWEEN ? AND ?""", [people[0][0]] + window)[0][0]
        repaid = self.metrics.rows("""SELECT SUM(p.amount) FROM payments p JOIN orders o USING(order_id) WHERE o.customer_id = ? AND o.payment_method = 'debt'
            AND o.status != 'cancelled' AND p.payment_date > o.order_date AND p.payment_date BETWEEN ? AND ?""", [people[0][0]] + window)[0][0]
        return {'type': 'list', 'answer': [money(added or 0), money(repaid or 0)]}

    def debtor_count(self, plain, as_of):
        end = debt_day(plain, as_of)
        if end is None or re.search(r'\bgop\b', plain) or not re.search(r'\bkhach\b', plain) or not re.search(r'\bno\b|cong no|thieu tien', plain) or not re.search(HOW_MANY + r'|\bdem\b', plain):
            return None
        if re.search(r'\btong\b|\bngay\b.*\bngay\b.*\bngay\b|nhat|\bqua\b \d', plain) or not re.search(HOW_MANY + r' (?:khach|nguoi)|\bso khach\b|\bdem khach|khach .*' + HOW_MANY + r' nguoi', plain):
            return None
        return {'type': 'number', 'answer': self.owed('COUNT(DISTINCT customer_id)', end)[0][0]}

    def plain_total(self, plain, as_of):
        """Whole-shop revenue or order count: every word must be a period or a generic word, so no condition can be missed."""
        span = any_period(plain, as_of)
        if span is None or not re.search(HOW_MANY + r'|\bla\b|\btong\b|\bdem\b|\bbiet\b|\bnhieu\b|\bcho toi\b|\bso (?:don|dh|bill)\b|\bdoanh thu\b', plain):
            return None
        orders, money_asked = re.search(r'\b(?:don|dh|bill)\b|\bo do\b', plain), re.search(r'doanh thu|dthu|\btien\b', plain)
        if bool(orders) == bool(money_asked):
            return None
        rest = re.sub(r'thang \d+/\d+|thang \d+|quy \d(?:(?:/| nam )\d+)?|\d+ ngay gan nhat|\d+ ngay qua|(?:ngay )?1/1|dau nam|thang nay|thang truoc|tuan nay|tuan truoc|tuan roi|hom nay|hom qua|hnay|hqua|bua nay|bua qua|nam nay', ' ', plain)
        if {word.strip('.,') for word in words_of(rest)} - GENERIC_TOTAL - {''}:
            return None
        # A period wholly outside the shop's dates has no data, which differs from zero (rulebook section 7).
        if span[1].isoformat() < self.metrics.first or span[0].isoformat() > self.metrics.last:
            return {'type': 'unanswerable', 'reason': 'period is outside the data'}
        if orders:
            return {'type': 'number', 'answer': self.metrics.order_count(*span, None)}
        return {'type': 'number', 'answer': money(self.metrics.revenue(*span, None))}

    def revenue_growth(self, plain, as_of):
        if not re.search(r'%|phan tram', plain) or not re.search(r'doanh thu|dthu|tien ban', plain) or re.search(FILTERS, plain):
            return None
        months = named_months(plain)
        if not months and re.search(r'\bthang \d', plain):
            return None
        last_year = re.search(r'cung ky nam|nam ngoai|nam truoc|nam trc|cung thang nam|nam roi', plain)
        if len(set(months)) == 2 and months[0][0] == months[1][0] and abs(months[0][1] - months[1][1]) == 1:
            month, year = months[0][0], max(months[0][1], months[1][1])
            slots = {'metric': 'growth', 'period': {'kind': 'month', 'month': month, 'year': year}, 'against': 'last_year'}
        elif len(set(months)) == 1 and last_year:
            slots = {'metric': 'growth', 'period': {'kind': 'month', 'month': months[0][0], 'year': months[0][1]}, 'against': 'last_year'}
        elif len(set(months)) == 1 and re.search(r'thang (?:lien truoc|lien trc|truoc|trc|ngay truoc)', plain):
            slots = {'metric': 'growth', 'period': {'kind': 'month', 'month': months[0][0], 'year': months[0][1]}}
        else:
            return None
        return self.metrics.compute(slots, as_of)
