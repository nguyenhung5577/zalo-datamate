"""Slot labels for training the form, from the odd train shops (dev) only; the even shops stay held out for the fair test.

A label is the slots that pass the current checks and give the reference answer. Candidates are the model's own earlier
replies and slots built by code from the question. Built candidates that disagree leave the question out; a question no
candidate answers is labelled "other", which teaches the form to decline. Each example is the prompt the runner sends,
for the question as asked and for each saved rewrite the guards keep, and every label is checked again on its own text."""
import argparse
import copy
from itertools import combinations
import json
from multiprocessing import Pool
from pathlib import Path
import re

from datamate.direct import Direct, spoken
from datamate.loading import date_hint, open_business
from datamate.metrics import CHANNEL_HINTS, PAYMENT_EVIDENCE, SLOT_REQUEST, Metrics, check_evidence, standard_months
from datamate.normalized import name_key
from datamate.records import load_json, read_jsonl
from datamate.rewrite import kept
from datamate.scoring import outcome

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / 'train' if (ROOT / 'train' / 'questions.jsonl').is_file() else ROOT.parent / 'train'
# What the model writes; the checks add code-read fields (amounts, units, months) that are not part of a label.
WRITTEN = ('metric', 'period', 'filters', 'top', 'of', 'against', 'count')
# A period the checks must replace with the one code reads, so a built candidate never guesses it.
UNREAD = {'kind': 'year', 'year': 1900}
METRICS = sorted(name[7:] for name in dir(Metrics) if name.startswith('metric_'))


def judged(direct, slots, text, as_of):
    """The slots as the model would write them after the checks, and the answer they give; None when refused."""
    engine, slots = direct.metrics, copy.deepcopy(slots)
    try:
        check_evidence(slots, text)
    except Exception:
        return None
    if slots.get('period') == UNREAD:
        return None
    try:
        engine.check_filters(slots.get('filters'), slots.get('asked'))
        label = {key: copy.deepcopy(slots[key]) for key in WRITTEN if key in slots and slots[key] not in (None, {})}
        return label, engine.compute(slots, as_of)
    except Exception:
        return False


def right(engine, slots, question, text, reference):
    """The label when the slots give the reference answer; None when the question's words refuse them;
    False when the words allow them but the shop's data refuses them or they answer wrong."""
    found = judged(engine, slots, text, question['as_of_date'])
    if not found:
        return found
    return found[0] if outcome({'id': question['id'], **found[1]}, reference) == 'correct' else False


def periods(plain):
    found = [UNREAD]
    found += [{'kind': 'month', 'month': int(m)} for m in dict.fromkeys(re.findall(r'\bthang (\d{1,2})\b(?!/)', plain)) if 1 <= int(m) <= 12]
    found += [{'kind': 'year', 'year': int(y)} for y in dict.fromkeys(re.findall(r'\bnam (\d{4})\b', plain))]
    for d, m, y in dict.fromkeys(re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', plain)):
        day = f'{y}-{int(m):02}-{int(d):02}'
        found += [{'kind': 'day', 'date': day}, {'kind': 'until', 'date': day}]
    return found + [{'kind': 'until_today'}]


def filter_options(direct, plain, named_products):
    found, labels = [{}], direct.metrics.labels()
    product = direct.product(plain)
    # The rules' matcher misses some names the model wrote in its replies; the checks still resolve each one.
    for name in dict.fromkeys(([product[1]] if product else []) + named_products):
        found.append({'product': name})
    named = {}
    for slot in ('channel', 'payment'):
        for label in labels[slot]:
            key = name_key(str(label), accents=True)
            hint = PAYMENT_EVIDENCE.get(label) if slot == 'payment' else next((h for name, h in CHANNEL_HINTS.items() if name in key), None)
            if hint and re.search(hint, plain):
                named.setdefault(slot, []).append(label)
    for slot in ('product_group', 'customer_group'):
        named[slot] = [label for label in labels[slot] if set(name_key(str(label), accents=True).split()) <= set(plain.split())]
    for slot, values in named.items():
        found += [{slot: value} for value in values]
        if slot in ('channel', 'payment'):
            found += [{slot: list(pair)} for pair in combinations(values, 2)]
    people = [row[0] for row in direct.metrics.rows('SELECT customer_name FROM customers') if row[0] and name_key(row[0], accents=True) in plain]
    return found + [{'customer': name} for name in people]


def built(direct, plain, named_products):
    """Every slot shape the question could mean, for the checks to sort out."""
    numbers = [int(n) for n in dict.fromkeys(re.findall(r'\b(\d{1,2})\b', plain)) if 1 < int(n) <= 20]
    for metric in METRICS:
        extras = [{}]
        if metric == 'count_over':
            extras = [{'count': count} for count in ('days', 'orders', 'customers', 'items')]
        elif metric in ('growth', 'shift_gap', 'best_day', 'top_products', 'top_customers'):
            extras = [{}, {'of': 'orders'}, {'of': 'revenue'}]
        if metric in ('top_products', 'top_customers', 'top_debtors'):
            extras = [{**extra, 'top': n} for extra in extras for n in numbers] + extras
        for filters in filter_options(direct, plain, named_products):
            for period in periods(plain):
                for extra in extras:
                    yield {'metric': metric, 'period': period, 'filters': copy.deepcopy(filters), **extra}


def shorthand_variant(text):
    """Deterministic Vietnamese chat shorthand / unaccented variant to harden slot-filling against noisy inputs."""
    s = text
    subs = [
        (r'(?i)\bdoanh thu\b', 'dthu'),
        (r'(?i)\bbao nhiêu\b', 'bnhiu'),
        (r'(?i)\btháng\s+(\d{1,2})/(\d{4})\b', r'th\1/\2'),
        (r'(?i)\btháng này\b', 'th này'),
        (r'(?i)\btháng trước\b', 'th trc'),
        (r'(?i)\bhôm nay\b', 'hnay'),
        (r'(?i)\bhôm qua\b', 'hqua'),
        (r'(?i)\bđược\b', 'dc'),
        (r'(?i)\bsản phẩm\b', 'sp'),
        (r'(?i)\bđơn hàng\b', 'đh'),
    ]
    for pat, rep in subs:
        s = re.sub(pat, rep, s)
    if s != text:
        return s
    # If already abbreviated, produce an accent-stripped chat wording.
    plain = name_key(text, accents=True)
    return plain if plain != text.lower() else None


def texts(question, runs, augment=True):
    """The question as asked, each saved rewrite the current guards keep, and an optional shorthand variant."""
    found = [question['question']]
    for run in runs:
        log = run.get(question['id']) or {}
        if kept(question['question'], log.get('rewrite')):
            found.append(log['rewrite'])
    if augment:
        noisy = shorthand_variant(question['question'])
        if noisy:
            found.append(noisy)
    return list(dict.fromkeys(found))


def shop(job):
    business, questions, folders, augment = job
    data, failure = open_business(DATA_ROOT, business, ROOT / '.cache' / 'metrics')
    if failure:
        return []
    direct = Direct(Metrics(data))
    references = {r['id']: r for r in read_jsonl(DATA_ROOT / 'answers.jsonl')}
    runs = [{log['id']: log for log in map(json.loads, (path.read_text() for path in (Path(folder) / 'logs').iterdir())) if log.get('business_id') == business}
            for folder in folders if (Path(folder) / 'logs').is_dir()]
    system = SLOT_REQUEST + '\nRECORDED LABELS:\n' + json.dumps(direct.metrics.labels(), ensure_ascii=False)
    examples = []
    for question in questions:
        reference, wordings = references[question['id']], texts(question, runs, augment=augment)
        # The model's own right replies come first: they are what it already says.
        label, source, answered, named = None, 'model', False, []
        for run in runs:
            log = run.get(question['id']) or {}
            asked = log['rewrite'] if kept(question['question'], log.get('rewrite')) else question['question']
            slots = load_json(log.get('model_output') or '')
            filters = slots.get('filters') if isinstance(slots, dict) else None
            if isinstance(filters, dict) and isinstance(filters.get('product'), str):
                named.append(filters['product'])
            label = right(direct, slots, question, asked, reference) if isinstance(slots, dict) else None
            answered = answered or label is not None
            if label:
                break
        if not label:
            plain = direct.shop_names(spoken(standard_months(name_key(question['question'], accents=True))))
            results = [right(direct, slots, question, question['question'], reference) for slots in built(direct, plain, named)]
            answered = answered or any(found is not None for found in results)
            found = {json.dumps(found, sort_keys=True) for found in results if found}
            meanings = {json.dumps({k: v for k, v in json.loads(item).items() if k in ('metric', 'filters')}, sort_keys=True) for item in found}
            # Several meanings that all fit, or slots that pass the checks yet answer wrong, are no sure label either way.
            if len(meanings) > 1 or (not found and answered):
                examples.append({'id': question['id'], 'business_id': business, 'source': 'ambiguous' if found else 'unsure'})
                continue
            if found:
                label = json.loads(min(found, key=len))
                label, source = {key: label[key] for key in WRITTEN if key in label}, 'built'
            else:
                label, source = {'metric': 'other'}, 'other'
        for text in wordings:
            # A label must still pass on each wording it is trained with.
            if source != 'other' and not right(direct, label, question, text, reference):
                continue
            user = 'Today is ' + question['as_of_date'] + '.\nQUESTION: ' + text + date_hint({**question, 'question': text})
            examples.append({'id': question['id'], 'business_id': business, 'source': source, 'rewritten': text != question['question'],
                             'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user},
                                          {'role': 'assistant', 'content': json.dumps(label, ensure_ascii=False, separators=(',', ':'))}]})
    return examples


if __name__ == '__main__':
    import multiprocessing as mp
    parser = argparse.ArgumentParser()
    default_q = ROOT / 'results' / 'fair' / 'dev.jsonl'
    if not default_q.is_file():
        default_q = DATA_ROOT / 'questions.jsonl'
    parser.add_argument('--questions', default=str(default_q))
    parser.add_argument('--runs', nargs='*', default=[str(ROOT / 'results' / 'fair' / name) for name in ('w4-dev', 'w3-dev', 'w2-dev', 'rw-dev', 'form-dev')])
    parser.add_argument('--output', default=str(ROOT / 'results' / 'training' / 'slots-dev.jsonl'))
    parser.add_argument('--include-held-out', action='store_true',
                        help='allow the even shops; only for a final model, which leaves no held-out shops to test it on')
    parser.add_argument('--no-augment', action='store_true', help='disable deterministic shorthand augmentation')
    args = parser.parse_args()
    questions = list(read_jsonl(Path(args.questions)))
    if not args.include_held_out:
        # Filter to odd shops automatically if train/questions.jsonl was passed as default, else enforce check.
        if Path(args.questions).resolve() == (DATA_ROOT / 'questions.jsonl').resolve():
            questions = [q for q in questions if int(q['business_id'][-3:]) % 2 == 1]
        elif any(int(q['business_id'][-3:]) % 2 == 0 for q in questions):
            raise SystemExit('labels come from odd shops only; even shops are the held-out test')
    shops = sorted({q['business_id'] for q in questions})
    jobs = [(b, [q for q in questions if q['business_id'] == b], args.runs, not args.no_augment) for b in shops]
    with mp.get_context('fork').Pool(min(8, len(jobs))) as pool:
        examples = [example for found in pool.map(shop, jobs) for example in found]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w') as stream:
        for example in examples:
            if 'messages' in example:
                stream.write(json.dumps(example, ensure_ascii=False) + '\n')
    counts = {}
    for example in examples:
        if example.get('rewritten'):
            continue
        counts[example['source']] = counts.get(example['source'], 0) + 1
    print(len(questions), 'questions;', counts, ';', sum('messages' in e for e in examples), 'examples ->', output)
