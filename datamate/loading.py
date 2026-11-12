"""Opening a shop's data and reading the question set."""
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from datamate.data import BusinessData
from datamate.normalized import NormalizedData
from datamate.records import read_jsonl

ROOT = Path(__file__).resolve().parents[1]


def load_questions(manifest, data=None):
    """All questions of a split, or those a manifest file lists, in shop order."""
    source = Path(data or ROOT / 'train') / 'questions.jsonl'
    if str(manifest) == 'all':
        return sorted(read_jsonl(source), key=lambda q: q['business_id'])
    split = json.loads(Path(manifest).read_text())
    assert hashlib.sha256(source.read_bytes()).hexdigest() == split['questions_sha256']
    ids = set(split['benchmark_question_ids'])
    questions = sorted([q for q in read_jsonl(source) if q['id'] in ids], key=lambda q: q['business_id'])
    assert ids and len(questions) == len(ids), 'Unknown question IDs'
    return questions


def open_business(split, business, cache):
    """Return (data, normalization failure); an unsupported layout has no data, so its questions abstain."""
    raw = BusinessData(split, business, cache)
    try:
        return NormalizedData(raw), None
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'


def plain(text):
    text = unicodedata.normalize('NFD', str(text).casefold())
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn').replace('đ', 'd')


def date_hint(question):
    """Spell out relative days in the question, since abbreviated forms lose their date filter."""
    text, today = plain(question['question']), date.fromisoformat(question['as_of_date'])
    hints = []
    for pattern, name, day in [(r'\b(hom qua|hoom qua|homqua|hqua|bua qua)\b', 'yesterday', today - timedelta(days=1)),
                               (r'\b(hom nay|hoom nay|homnay|hnay|bua nay)\b', 'today', today)]:
        if re.search(pattern, text):
            hints.append(f"The question refers to {name}, {day.isoformat()}: filter order_date = '{day.isoformat()}' "
                         'unless it asks for a range ending that day.')
    if re.search(r'\bcung ky\b', text):
        month_start = today.replace(day=1)
        previous_end = month_start - timedelta(days=1)
        same_day = previous_end.replace(day=min(today.day, previous_end.day))
        hints.append(f'Same period last month: this period is {month_start.isoformat()} to {today.isoformat()}, '
                     f'compared with {previous_end.replace(day=1).isoformat()} to {same_day.isoformat()}. '
                     'Change % = (this - last) * 100 / last.')
    return '\nDATES IN THIS QUESTION: ' + ' '.join(hints) if hints else ''
