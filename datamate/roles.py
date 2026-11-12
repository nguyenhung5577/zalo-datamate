"""Map each source column to a canonical role from the README glossary or the column name.

Exports name columns freely; the README glossary uses a small description vocabulary, so it is the
primary signal. Columns without a role are hidden so stray names cannot trigger the wrong builder path.
"""
import re
import unicodedata

# Canonical column names are the names datamate.normalized reads.
CANONICAL = {
    'order_id': 'Mã hóa đơn', 'order_date': 'Ngày', 'order_time': 'Giờ', 'status': 'Trạng thái',
    'customer_id': 'Mã KH', 'customer_name': 'Tên khách hàng', 'payment_method': 'Phương thức thanh toán',
    'invoice_total': 'Phải thu', 'goods_total': 'Tổng tiền hàng', 'order_discount': 'Giảm giá HĐ',
    'customer_group': 'Nhóm khách', 'channel': 'Kênh bán', 'stock_on_hand': 'Tồn kho',
    'order_note': 'Ghi chú đơn', 'short_name': 'Ghi tắt', 'product_alias': 'Tên gọi khác',
    'debt_left': 'Còn nợ', 'table_name': 'Bàn', 'area': 'Khu vực', 'guest_count': 'Số khách',
    'product_id': 'Mã hàng', 'product_name': 'Tên hàng', 'product_group': 'Nhóm hàng hóa',
    'unit': 'Đơn vị tính', 'base_unit': 'Đơn vị cơ bản', 'large_unit': 'Đơn vị quy đổi', 'factor': 'Hệ số',
    'quantity': 'Số lượng', 'basic_quantity': 'Số lượng quy đổi', 'unit_price': 'Đơn giá',
    'line_amount': 'Thành tiền', 'line_no': 'STT',
    'ingredient': 'Hoạt chất', 'prescription': 'Thuốc kê đơn',
    'payment_id': 'Mã GD', 'payment_date': 'Thời điểm', 'payment_amount': 'Tiền',
    'import_id': 'Mã nhập hàng', 'import_date': 'Ngày nhập', 'purchase_price': 'Giá nhập',
    'purchase_quantity': 'SL nhập', 'lot_id': 'Mã lô', 'imported_quantity': 'Số lượng nhập',
    'returned_quantity': 'Số lượng trả',
}


def key(text):
    text = str(text)
    # camelCase and acronym runs: MaHoaDon -> Ma Hoa Don, SLQuyDoi -> SL Quy Doi.
    text = ''.join(' ' + c if i and c.isupper() and (text[i - 1].islower() or text[i - 1].isupper()
                   and i + 1 < len(text) and text[i + 1].islower()) else c for i, c in enumerate(text))
    text = ''.join(c for c in unicodedata.normalize('NFD', text.casefold()) if unicodedata.category(c) != 'Mn')
    return ' '.join(re.sub(r'[^a-z0-9%]+', ' ', text.replace('đ', 'd')).split())


# Glossary descriptions, matched as prefixes of the normalized description; longest first.
DESCRIPTIONS = sorted({
    'nhom khach': 'customer_group', 'kenh ban': 'channel', 'ton kho': 'stock_on_hand',
    'ma don': 'order_id', 'so hoa don': 'order_id', 'so thu tu don': 'order_id', 'ngay sua': 'order_date',
    'tong tien ca phieu': 'goods_total', 'ngay ban': 'order_date',
    'thoi diem dat don': 'order_time', 'gio dat don': 'order_time', 'trang thai don': 'status',
    'trang thai phieu nhap': 'status', 'ma khach': 'customer_id', 'ten khach': 'customer_name',
    'ho ten khach': 'customer_name', 'hinh thuc thanh toan': 'payment_method',
    'tong tien don': 'invoice_total', 'so khach phai tra cho ca don': 'invoice_total',
    'khach can tra cho ca don': 'invoice_total', 'tong tien hang cua': 'goods_total',
    'tien hang cua don': 'goods_total', 'giam gia cua ca don': 'order_discount', 'giam gia': 'discount', 'ghi chu cua don': 'order_note',
    'ma hang': 'product_id', 'ten hang': 'product_name', 'hang ban': 'product_name',
    'nhom hang': 'product_group', 'don vi tinh': 'unit', 'don vi nhap': 'unit',
    'don vi co ban': 'base_unit', 'don vi lon': 'large_unit', 'quy cach dong goi': 'large_unit', 'quy cach': 'large_unit',
    'he so': 'factor', 'bang bao nhieu': 'factor',
    'ten goi khac': 'product_alias', 'ten khac cua hang': 'product_alias', 'biet danh': 'product_alias',
    'so khach con no cua don': 'debt_left', 'khach con no cua ca don': 'debt_left',
    'so tien cua don chua thu': 'debt_left', 'so tien con no': 'debt_left', 'tien con no': 'debt_left', 'con no': 'debt_left',
    'khu vuc': 'area', 'so khach cua don': 'guest_count', 'so luong khach': 'guest_count', 'so khach ngoi': 'guest_count',
    'so luong da doi ra don vi co ban': 'basic_quantity', 'so luong ban lay tu lo': 'quantity',
    'so luong khach tra lai': 'returned_quantity', 'so luong nhap don vi co ban': 'imported_quantity',
    'so luong nhap': 'purchase_quantity', 'so luong': 'quantity', 'don gia': 'unit_price',
    'gia nhap mot don vi': 'purchase_price', 'thanh tien sau giam': 'line_amount',
    'thanh tien cua dong': 'line_amount', 'tien nhap cua ca dong': 'line_amount',
    'so thu tu dong trong': 'line_no', 'hoat chat': 'ingredient', 'thuoc ke don': 'prescription',
    'ma phieu thu': 'payment_id', 'thoi diem thu': 'payment_date', 'so tien': 'payment_amount',
    'ma phieu nhap': 'import_id', 'ngay nhap': 'import_date', 'so lo': 'lot_id',
}.items(), key=lambda item: -len(item[0]))

# Column-name fallback when the glossary is silent; exact normalized names only.
NAMES = {
    'short_name': 'ghi tat',
    'product_alias': 'ten goi khac|ten khac|alias',
    'debt_left': 'con no|du no|chua tra|con lai|con phai thu|con thieu|tien no|cong no',
    'area': 'khu vuc|tang|phong khu',
    'guest_count': 'so khach|sl khach|so nguoi',
    'customer_group': 'nhom khach|nhom khach hang|loai khach|loai khach hang|kieu khach',
    'channel': 'kenh|kenh ban|kenh ban hang|kenh dat|ban qua|loai ban|nguon|nguon don',
    'stock_on_hand': 'ton kho|ton cuoi|ton cuoi ky|ton thuc',
    'order_id': 'don so|ma don|ma don hang|ma hoa don|ma giao dich|ma bill|bill|ma chung tu|so ct|so hd|so hoa don|order id|hd',
    'order_date': 'ngay hd|ngay|thoi gian|ngay ban|ngay lap|ngay giao dich|ngay mua|ngay dat hang|ngay thang|date|ngayban',
    'order_time': 'gio|thoi gian tao|gio tao don|gio ban|thoi gian lap',
    'status': 'trang thai giao|trang thai|tinh trang|status|ket qua|xu ly|ghi chu tt|tt don|tien do|trang thai don'
              '|trang thai don hang|trang thai xu ly|tinh trang don',
    'customer_id': 'ma the|ma kh|ma khach|ma khach hang|ma khach mua|ma kh mua|customer id|so kh|ma nguoi mua',
    'customer_name': 'khach hang mua|ten khach|ten khach hang|khach hang|khach|ben mua|doi tac|ten kh|ho ten khach'
                     '|khach hang ten',
    'payment_method': 'phuong thuc thanh toan|phuong thuc tt|hinh thuc thanh toan|httt|pttt|tra bang'
                      '|thanh toan bang',
    'invoice_total': 'tong sau giam gia|phai thu|tong bill|tong don|tong thanh toan',
    'discount': 'giam gia|ck|tien giam|chiet khau',
    'goods_total': 'tong tien hang|tien hang|tien hang truoc giam|gia tri hang',
    'product_id': 'ma h hoa|ma hang|ma sp|ma san pham|ma hh|id sp|sku|ma sku|ma hang hoa|ma mon|ma mat hang'
                  '|ma hang ban|ma san pham ban',
    'product_name': 'ten hang|ten san pham|ten sp|mat hang|hang|hang hoa|hang ban|san pham|mon|ten mon'
                    '|ten mat hang|ten hang hoa|product|chi tiet hang',
    'product_group': 'nhom hang|nhom hang hoa|nhom hang 3 cap|danh muc|loai san pham|phan loai|loai hang',
    'unit': 'dv|dvt|don vi tinh|don vi|unit|dv tinh|d vi|dvt nhap|don vi nhap|dv nhap',
    'base_unit': 'don vi co ban|dv goc|dv nho|don vi goc|don vi co so',
    'large_unit': 'don vi quy doi|dv lon',
    'factor': 'he so|ti le quy doi|he so qd|he so quy doi|quy doi|bang',
    'quantity': 'so luong|sl|sl ban|s l|sluong|slg|so luong ban|so luong san pham|so luong hang',
    'basic_quantity': 'sl dv co ban|quy ra dv le|sl le|so luong le|sl quy doi|so luong quy doi|sl quy ra le|sl co ban',
    'unit_price': 'dg|gia niem yet|don gia|gia|gia ban|gia 1 dv|gia le|gia san pham|gia ban le|gia don vi|price'
                  '|don gia ban|don gia d',
    'line_amount': 'tt|thanh tien|t tien|amount|tri gia|tien ban|thanh tien d|tong tien',
    'line_no': 'stt|tt dong|no|so thu tu|thu tu|so dong',
    'ingredient': 'hoat chat|thanh phan chinh',
    'prescription': 'thuoc ke don|ban theo don|ke don',
    'payment_id': 'ma thanh toan|ma phieu thu|phieu thu|ma tt|ma phieu',
    'payment_date': 'ngay tt|thoi diem|thoi gian thu|ngay thu|ngay gio thu',
    'payment_amount': 'tien thu|so tien thu|so tien|gia tri',
    'import_id': 'ma phieu nhap|ma nhap hang|ma don nhap|so phieu|so phieu nhap',
    'import_date': 'ngay nhap|ngay nhap kho',
    'purchase_price': 'gia nhap|don gia nhap',
    'purchase_quantity': 'sl nhap',
    'lot_id': 'ma lo|lo',
    'imported_quantity': 'so luong nhap|sl nhap quy doi|sl nhap dvt co ban|so luong dvt co ban',
    'returned_quantity': 'so luong tra|sl tra quy doi|sl tra dvt co ban|so luong tra dvt co ban',
}
NAME_ROLES = {name: role for role, names in NAMES.items() for name in names.split('|')}

# Bare descriptions such as "tên" depend on what the table lists.
CONTEXT = {'ten': ('customer_name', 'product_name')}


def section_pattern(heading):
    """`BanHang_T<tháng>_<năm>.xlsx`, sheet `X` -> regex over "file|sheet|table" identifiers."""
    names = re.findall(r'`([^`]+)`', heading)
    file = '.*'.join(re.escape(part) for part in re.split(r'<[^>]*>', names[0]))
    sheet = re.search(r'sheet\s+`([^`]+)`', heading)
    return file + (r'\|.*\b' + re.escape(sheet[1]) + r'\b' if sheet else '')


def glossary(readme):
    """Return {(section pattern, column): description} from a business README."""
    found, section = {}, None
    for line in readme.splitlines():
        grouping = re.search(r'các dòng cùng \*\*(.+?)\*\* là một đơn', line)
        if grouping:
            found[('.*', grouping[1])] = 'mã đơn'
        if re.match(r'##\s+.*?`[^`]+`', line):
            section = section_pattern(line)
        elif line.startswith('#'):
            section = None
        item = re.match(r'\s*-\s+\*\*(.+?)\*\*\s*:\s*(.+)$', line)
        if item and section:
            found[(section, item[1].strip())] = item[2].strip()
        for table, column, description in re.findall(r'`(\w+)\.(\w+)`\s+là\s+([^;`]+)', line):
            found[(r'.*\b' + table, column)] = description.strip()
    return found


def role_of(description=None, name=None, table=''):
    if description:
        text = key(description)
        if text in CONTEXT:
            customer, product = CONTEXT[text]
            return customer if 'khach' in key(table) else product
        for prefix, role in DESCRIPTIONS:
            if text == prefix or text.startswith(prefix + ' '):
                return role
    if name is None:
        return None
    low = ' '.join(str(name).split()).casefold()
    if low in ('nợ', 'tiền nợ', 'còn nợ', 'dư nợ'):
        return 'debt_left'
    if low in ('bàn', 'số bàn', 'mã bàn', 'tên bàn', 'phòng/bàn', 'bàn số'):
        return 'table_name'
    if key(name) in ('nhom', 'hang') and 'khach' in key(table):
        return 'customer_group'
    if key(name) in ('ten', 'ten goi'):
        context = key(table)
        if 'khach' in context:
            return 'customer_name'
        if 'san pham' in context or 'hang hoa' in context:
            return 'product_name'
        return None
    return NAME_ROLES.get(key(name))


def column_roles(table, columns, readme_glossary):
    """Map one source table's columns to roles; at most one column per role, glossary first."""
    identity = table.get('source', '') + '|' + table.get('sheet', '') + '|' + table['table']

    def described(column):
        for (section, name), description in readme_glossary.items():
            if name == column and re.fullmatch(section + r'(\|.*)?', identity, re.S):
                return description
        return None

    chosen = {}
    for column in columns:
        description = described(column)
        role = role_of(description, None, table['table']) if description else None
        rank = 0 if role else 1
        role = role or role_of(None, column, table['table'])
        if role and (role not in chosen or rank < chosen[role][1]):
            chosen[role] = (column, rank)
    # Conversion or auxiliary tables sometimes store the product code under a barcode header when no product ID/name column exists.
    if 'product_id' not in chosen and 'product_name' not in chosen:
        for column in columns:
            if key(column) in ('ma vach', 'ma vach ean', 'barcode', 'ean'):
                chosen['product_id'] = (column, 1)
                break
    if 'factor' in chosen and 'large_unit' not in chosen and 'unit' not in chosen:
        for column in columns:
            if key(column) == 'quy cach':
                chosen['large_unit'] = (column, 1)
                break
    # A sales book with no status column records returns and cancellations in its plain note, even when the README lists no columns.
    if 'order_id' in chosen and {'quantity', 'product_name', 'product_id'} & set(chosen) and not {'status', 'order_note'} & set(chosen):
        for column in columns:
            if key(column) == 'ghi chu':
                chosen['order_note'] = (column, 1)
                break
    # A generic date or amount belongs to the document the table records.
    kind = ('import' if 'import_id' in chosen else
            'payment' if {'payment_id', 'payment_amount'} & set(chosen) and 'quantity' not in chosen else
            'header' if {'order_id', 'order_date'} <= set(chosen) and not {'quantity', 'product_id', 'product_name'} & set(chosen)
            else None)
    moves = {'import': {'order_date': 'import_date', 'unit_price': 'purchase_price'},
             'payment': {'order_date': 'payment_date', 'line_amount': 'payment_amount'},
             'header': {'line_amount': 'invoice_total', 'discount': 'order_discount'}}.get(kind, {})
    for old, new in moves.items():
        if old in chosen and new not in chosen:
            chosen[new] = chosen.pop(old)
    chosen.pop('discount', None)  # only an order-level discount feeds the invoice total
    return {column: role for role, (column, _) in chosen.items()}


def canonicalize(table, records, readme_glossary):
    """Rename role columns to canonical names and hide every other source column."""
    if not records:
        return records
    columns = [c for c in records[0] if not c.startswith('__dm_')]
    # Repeated header lines inside a sheet are not records.
    records = [r for r in records if sum(key(r[c]) == key(c) for c in columns if r[c]) < 2]
    roles = column_roles(table, columns, readme_glossary)
    rename = {c: CANONICAL[roles[c]] if c in roles else 'source:' + c for c in columns}
    return [{rename.get(k, k): v.strip() if isinstance(v, str) else v for k, v in r.items()} for r in records]
