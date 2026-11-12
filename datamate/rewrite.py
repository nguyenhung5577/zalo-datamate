"""The model restates a question in plain written Vietnamese before the rules read it; it never answers."""
import re

from datamate.normalized import name_key
from datamate.records import load_json

REWRITE_REQUEST = """Rewrite a Vietnamese shop owner's question as plain written Vietnamese with full words and accents.
Expand chat shorthand, acronyms and typos into the full words they stand for. Keep every number, date, amount,
product name, customer name and code exactly as written, and keep the meaning: do not add, drop or answer anything.
If the question is already plain, return it unchanged. Return {"question": "..."} only.
Examples:
"thang 3/2025 khach tra ck bn tien" -> {"question": "Tháng 3/2025 khách trả chuyển khoản bao nhiêu tiền?"}
"hđ thanh toán tm tuần rồi mấy cái" -> {"question": "Tuần rồi có mấy hóa đơn thanh toán tiền mặt?"}
"sl Pepsi 330ml bán qua đt tháng này" -> {"question": "Số lượng Pepsi 330ml bán qua điện thoại tháng này là bao nhiêu?"}
"""


def numbers(text):
    return sorted(re.findall(r'\d+', text))


def period(text):
    """The period code reads from the words, so a rewrite that turns hqua into hang qua is caught."""
    from datamate.direct import spoken
    from datamate.metrics import Unsupported, fix_period, standard_months
    slots = {}
    try:
        fix_period(slots, spoken(standard_months(name_key(text, accents=True))))
    except Unsupported as exc:
        return str(exc)
    return slots.get('period')


# What a question counts. A rewrite may add one (dt becomes doanh thu) but never lose one (dthu became da thu, and
# "bao nhieu hop chan, le bao nhieu goi" became "bao nhieu hop, bao nhieu goi", one number instead of whole and loose).
CONCEPTS = [r'doanh thu|dthu', r'\b(?:don|dh|bill)\b|hoa don', r'\bno\b|cong no', r'\bnhap\b', r'\bhuy\b', r'%|phan tram|ti le|ty le',
            r'\bdot\b', r'\bchan\b', r'\ble\b']


# A concept the rewrite adds must come from shorthand the original holds. "dt" is doanh thu (or dien thoai); Gemma 4
# read it as don hang, turning eight revenue questions on the fair noisy set into order counts.
ADDED_FROM = {CONCEPTS[0]: r'\bdt\b|\bds\b|tien ban', CONCEPTS[1]: r'\bhd\b|\bdh\b'}
# The word tien (money) other than tien mat (cash), which only names a payment way.
MONEY = re.compile(r'\btien\b(?! mat)')


def concepts(text):
    from datamate.direct import spoken
    plain = spoken(name_key(text, accents=True))
    return {pattern for pattern in CONCEPTS if re.search(pattern, plain)}


def named(text):
    """The payment ways and channels a question names; a rewrite may spell one out (dt as dien thoai) but never lose one (xi o di became tien mat)."""
    from datamate.direct import spoken
    from datamate.metrics import CHANNEL_HINTS, PAYMENT_EVIDENCE
    plain = spoken(name_key(text, accents=True))
    return {label for label, hint in {**PAYMENT_EVIDENCE, **CHANNEL_HINTS}.items() if re.search(hint, plain)}


def months(text):
    """Every month named, so "3 thang 5/2025, 6/2025" (three months) turning into "thang 3, thang 5/2025" is caught."""
    from datamate.direct import spoken
    from datamate.metrics import standard_months
    return sorted(re.findall(r'\bthang (\d{1,2})\b', spoken(standard_months(name_key(text, accents=True)))))


def groups(text):
    return sorted(re.findall(r'\bnhom (\w+)', name_key(text, accents=True)))


def rewrite(model, question):
    """The question in plain words, or None when the rewrite changes a number or name the question holds."""
    raw, _, _ = model.chat([{'role': 'system', 'content': REWRITE_REQUEST}, {'role': 'user', 'content': question['question']}], 60, max_tokens=200)
    text = load_json(raw).get('question')
    return (text if kept(question['question'], text) else None), raw


def kept(original, text):
    """Whether a rewrite keeps every number, period, month, group and metric word of the original."""
    if not isinstance(text, str) or not text.strip():
        return False
    # A changed number moves a date or an amount, so the rewrite is dropped rather than trusted.
    if numbers(text) != numbers(original):
        return False
    # The model misreads some time words (hqua as hang qua), which silently changes the period asked about.
    if period(text) != period(original) or months(text) != months(original):
        return False
    # Group names are shop words the model does not know (nhom di ung became cua hang dich vu).
    if groups(text) != groups(original) or concepts(original) - concepts(text) or named(original) - named(text):
        return False
    plain = name_key(original, accents=True)
    if any(not re.search(ADDED_FROM[c], plain) for c in concepts(text) - concepts(original) if c in ADDED_FROM):
        return False
    # "dt" without accents is doanh thu far more often than dien thoai: both models turned "hqua dt duoc bao nhieu"
    # (yesterday's revenue) into phone-channel revenue. A phone channel the question does not spell out is dropped.
    if 'phone' in named(text) - named(original):
        return False
    # Money the question never asks about: "so bill hom nay" (how many bills) became "so tien hoa don" (bill amount).
    if MONEY.search(name_key(text, accents=True)) and not re.search(r'\btie+n|\bdt\b|\bds\b|\bgia\b', plain):
        return False
    return name_key(text, accents=True) != name_key(original, accents=True)
