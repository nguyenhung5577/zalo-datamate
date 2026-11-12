"""Decline questions whose rulebook line lost a term the code was written against. Additions to a line pass: public only ever extended train's shared lines."""
import json
import re
from pathlib import Path

from datamate.normalized import name_key

KNOWN = Path(__file__).with_name('rulebook_terms.json')
# Lines true of one kind of question. Lines every answer rests on (what counts as revenue, how periods read) are left out,
# since a change there would blank everything rather than the questions it touches.
SCOPES = {
    'Đơn bị huỷ': r'\bhuy\b', 'Tỉ lệ đơn huỷ trong kỳ': r'\bhuy\b', 'Ca sáng, ca chiều': r'\bca (?:sang|chieu)\b', 'Tết năm Y, dịp Tết, mùa Tết': r'\btet\b',
    'Bán chạy': r'\bchay\b|\bban\b.*\bnhat\b', 'Hàng lời nhất': r'\bloi\b.*\bnhat\b', 'Nhân viên bán giỏi nhất': r'\bgioi nhat\b', 'Khách trung thành nhất': r'trung thanh',
    'Khách sộp nhất': r'\bsop\b', 'Khách hay đổi ý nhất': r'doi y', 'Mặt hàng được ưa chuộng nhất': r'ua chuong', 'Nhân viên, thợ chăm chỉ nhất': r'cham chi',
    'Ngày đắt khách nhất': r'dat khach', 'Giờ cao điểm': r'cao diem', 'Đơn được tính vào nợ': r'\bno\b', 'Nợ một đơn tại ngày X': r'\bno\b', 'Nợ theo khách': r'\bno\b',
    'Đơn COD, đơn bán qua sàn TMĐT': r'\bno\b', 'Tuổi nợ': r'tuoi no', 'Nợ thêm, trả nợ, thu nợ trong kỳ': r'\bno\b', 'Tiền nhập hàng trong kỳ': r'\bnhap\b',
    'Nhà cung cấp chính': r'nha cung cap', 'Nhập trừ bán, chênh nhập và bán': r'\bnhap\b.*\bban\b', 'Giá trị hàng tồn kho': r'\bton\b|\bkho\b', 'Mặt hàng hết hàng cuối ngày': r'het hang',
    'Mặt hàng không bán được trong kỳ (ế)': r'\be\b|khong ban', 'Hiệu ứng tăng giá': r'(?:tang|len|doi) gia', 'Lãi gộp': r'\blai gop\b|\bloi gop\b|loi nhuan gop', 'Lãi ròng một tháng': r'lai rong|loi that',
    'Size M, size L': r'\bsize\b', 'Thuốc kê đơn': r'ke don', 'Hàng hết hạn trong N ngày tới': r'het han|han dung', 'Doanh thu theo hoạt chất': r'hoat chat', 'Giao nhiều đợt': r'\bdot\b',
    'Số sữa, size bỉm': r'\bsua\b|\bbim\b', 'Khách thành viên mới trong tháng': r'thanh vien', 'Lượt xe, phiếu': r'\bluot\b|\bphieu\b', 'Thu máy cũ': r'may cu', 'Trả góp': r'\bgop\b',
}


def lines_of(text):
    """Each table line of a rulebook by its first cell; a label used twice, like Bảo hành, keeps both texts together."""
    found = {}
    for row in re.findall(r'^\|([^|\n]+)\|(.+)\|\s*$', text, re.M):
        label = row[0].strip()
        found[label] = found.get(label, '') + ' ' + row[1]
    return found


def terms_of(line):
    """The bold phrases and numbers of a line: the parts that state its meaning."""
    return sorted({name_key(term, accents=True) for term in re.findall(r'\*\*(.+?)\*\*', line)} | set(re.findall(r'\b\d+\b', line)))


class Guard:
    def __init__(self, rulebook):
        known, lines = json.loads(KNOWN.read_text(encoding='utf-8')), lines_of(rulebook)
        have = {label: name_key(lines[label], accents=True) for label in lines}
        self.changed = [label for label, terms in known.items() if label in have and any(term not in have[label] for term in terms)]
        self.scopes = [SCOPES[label] for label in self.changed if label in SCOPES]

    def blocks(self, question):
        plain = name_key(question['question'], accents=True)
        return any(re.search(scope, plain) for scope in self.scopes)
