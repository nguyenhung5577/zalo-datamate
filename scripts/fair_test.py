"""A fair test on the even train shops, which no rule was written from.

prepare  writes the held-out questions as asked (clean), in chat shorthand (noisy) and in other common words (para) to results/fair/,
         and the odd shops (dev) for tuning.
rules    answers both by code rules alone, with no model, and scores them.
score    scores runner output folders: run_metric_layer --data train --questions results/fair/<set>.jsonl [--rules off].
replay   re-judges a rules-off folder's saved model replies with the current code, so check and metric changes need no GPU.

Penalized accuracy is (right - wrong) / questions, the public score's formula.
"""
import argparse
from collections import Counter
import contextlib
import hashlib
import io
import json
from pathlib import Path
import random
import re
import unicodedata

from datamate.records import read_jsonl
from datamate.scoring import outcome

ROOT = Path(__file__).resolve().parents[1]
TRAIN_DIR = ROOT / 'train' if (ROOT / 'train').exists() else ROOT.parent / 'train'
FAIR = ROOT / 'results' / 'fair'

# Common Vietnamese chat shorthand. Picked from general usage, not from any question, so it stands in for
# users the rules never read; the rules already know some of these forms and not others.
SHORTHAND = [('doanh thu', 'dt'), ('số lượng', 'sl'), ('khách hàng', 'kh'), ('sản phẩm', 'sp'), ('mặt hàng', 'mh'),
             ('được', 'đc'), ('không', 'ko'), ('bao nhiêu', 'bn'), ('tháng', 'th'), ('hôm qua', 'hqua'), ('hôm nay', 'hnay'),
             ('trước', 'trc'), ('với', 'vs'), ('nhân viên', 'nv'), ('đơn hàng', 'đh'), ('nhiều', 'nhìu'),
             ('cửa hàng', 'shop'), ('như thế nào', 'ntn'), ('bây giờ', 'bh'), ('gì', 'j')]


# Common Vietnamese synonyms with the same meaning, also picked from general usage and not from any question.
# Para tests whether the word checks are too narrow for users who phrase things in other words.
SYNONYMS = [('doanh thu', 'tiền bán hàng'), ('nhân viên', 'người bán'), ('nhà cung cấp', 'nhà phân phối'), ('khách hàng', 'người mua'),
            ('bao nhiêu', 'mấy'), ('tổng cộng', 'tổng'), ('còn nợ', 'còn thiếu'), ('cửa hàng', 'tiệm'), ('bán được', 'bán ra'),
            ('đơn hàng', 'hóa đơn'), ('mặt hàng', 'món hàng'), ('cao nhất', 'lớn nhất'), ('hôm qua', 'ngày hôm qua'), ('cho hỏi', 'cho tôi biết')]


def held_out(business):
    return int(business.split('_')[1]) % 2 == 0


def plain(text):
    text = unicodedata.normalize('NFD', text)
    return unicodedata.normalize('NFC', ''.join(c for c in text if unicodedata.category(c) != 'Mn').replace('đ', 'd').replace('Đ', 'D'))


def noisy(question):
    """The same question in shorthand, the same way every run: each form swaps in half the time, and half the questions lose accents."""
    pick = random.Random(question['id'])
    text = unicodedata.normalize('NFC', question['question'])
    for long, short in SHORTHAND:
        if pick.random() < 0.5:
            text = re.sub(r'(?<!\w)' + long + r'(?!\w)', short, text, flags=re.IGNORECASE)
    return plain(text) if pick.random() < 0.5 else text


def para(question):
    """The same question with half the synonyms swapped in, the same way every run."""
    pick = random.Random('para ' + question['id'])
    text = unicodedata.normalize('NFC', question['question'])
    for word, other in SYNONYMS:
        if pick.random() < 0.5:
            text = re.sub(r'(?<!\w)' + word + r'(?!\w)', other, text, flags=re.IGNORECASE)
    return text


def prepare():
    every = read_jsonl(TRAIN_DIR / 'questions.jsonl')
    questions = [q for q in every if held_out(q['business_id'])]
    FAIR.mkdir(parents=True, exist_ok=True)
    # The odd shops, which rules were written from, are for tuning; their score says nothing about new shops.
    with (FAIR / 'dev.jsonl').open('w') as stream:
        for question in every:
            if not held_out(question['business_id']):
                stream.write(json.dumps(question, ensure_ascii=False) + '\n')
    for name, change in (('clean', lambda q: q['question']), ('noisy', noisy), ('para', para)):
        with (FAIR / f'{name}.jsonl').open('w') as stream:
            for question in questions:
                stream.write(json.dumps({**question, 'question': change(question)}, ensure_ascii=False) + '\n')
    print(len(questions), 'held-out questions in', len({q['business_id'] for q in questions}), 'shops ->', FAIR)


def score(folder):
    """Right, wrong and abstain by the stage that answered: rules, model, or none."""
    gold = {r['id']: r for r in read_jsonl(TRAIN_DIR / 'answers.jsonl')}
    tally = Counter()
    for found in read_jsonl(Path(folder) / 'submission.jsonl'):
        log = json.loads((Path(folder) / 'logs' / (hashlib.sha256(found['id'].encode()).hexdigest() + '.json')).read_text())
        result = outcome(found, gold[found['id']])
        metric = (log.get('slots') or {}).get('metric')
        stage = 'none' if result == 'abstain' else 'rules' if metric == 'direct' else 'sql_fallback' if metric == 'sql_fallback' else 'model'
        tally[stage, result] += 1
    n = sum(tally.values())
    right, wrong = sum(v for (_, r), v in tally.items() if r == 'correct'), sum(v for (_, r), v in tally.items() if r == 'wrong')
    print(f'{Path(folder).name}: {n} questions, right {right}, wrong {wrong}, abstain {n - right - wrong}, penalized accuracy {(right - wrong) / n:.4f}')
    for stage in ('rules', 'model', 'sql_fallback'):
        if tally[stage, 'correct'] or tally[stage, 'wrong']:
            print(f'  by {stage}: right {tally[stage, "correct"]}, wrong {tally[stage, "wrong"]}')


def replay(folder, with_rules=False):
    """Re-judge a rules-off run's saved model replies with the current checks and metrics, into <folder>-replay. Uses no model.

    With rules, the code rules answer first, as in the real pipeline: on the question as asked, then on its saved rewrite."""
    from datamate.direct import Direct
    from datamate.loading import open_business
    from datamate.metrics import Metrics, Unsupported, check_evidence
    from datamate.records import load_json, write_json
    from datamate.rewrite import kept
    folder, engines = Path(folder), {}
    output = folder.with_name(folder.name + ('-pipeline' if with_rules else '-replay'))
    (output / 'logs').mkdir(parents=True, exist_ok=True)
    with (output / 'submission.jsonl').open('w') as stream:
        for log_path in sorted((folder / 'logs').iterdir()):
            record = json.loads(log_path.read_text())
            business = record['business_id']
            if business not in engines:
                data, failure = open_business(TRAIN_DIR, business, ROOT / '.cache' / 'metrics')
                engines[business] = Direct(Metrics(data)) if not failure else None
            final = {'id': record['id'], 'type': 'abstain'}
            # A saved rewrite the current guards would drop is replayed as the question was asked.
            asked = record['rewrite'] if kept(record['question'], record.get('rewrite')) else record['question']
            try:
                found = None
                if with_rules:
                    for text in dict.fromkeys([record['question'], asked]):
                        found = found or engines[business].answer({**record, 'question': text})
                if found is not None:
                    slots = {'metric': 'direct'}
                else:
                    slots = load_json(record['model_output'])
                    check_evidence(slots, asked)
                    found = engines[business].metrics.compute(slots, record['as_of_date'])
                final = {'id': record['id'], **found}
            except (Unsupported, KeyError, TypeError, ValueError, AttributeError):
                slots = None
            write_json(output / 'logs' / log_path.name, {'id': record['id'], 'slots': slots, 'final_answer': final})
            stream.write(json.dumps(final, ensure_ascii=False) + '\n')
    score(output)


def rules():
    """Code rules only: the runner with no model answers what the rules answer and abstains on the rest."""
    from scripts import run_metric_layer as runner
    runner.watch_rulebook((TRAIN_DIR / 'rulebook.md').read_text(encoding='utf-8-sig'))
    for name in ('clean', 'noisy', 'para'):
        from datamate.provenance import scope
        # The default scope keeps its old folder names; DATAMATE_RULES=train writes beside them.
        output = FAIR / (f'rules-{name}' if scope() == 'all' else f'rules-{scope()}-{name}')
        (output / 'logs').mkdir(parents=True, exist_ok=True)
        questions = sorted(read_jsonl(FAIR / f'{name}.jsonl'), key=lambda q: q['business_id'])
        with contextlib.redirect_stdout(io.StringIO()):
            runner.answer_all(questions, TRAIN_DIR, ROOT / '.cache' / 'metrics', output, None)
        score(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('step', choices=['prepare', 'rules', 'score', 'replay'])
    parser.add_argument('folders', nargs='*')
    parser.add_argument('--with-rules', action='store_true', help='replay: code rules answer first, as in the real pipeline')
    args = parser.parse_args()
    if args.step == 'prepare':
        prepare()
    elif args.step == 'rules':
        rules()
    else:
        for folder in args.folders:
            score(folder) if args.step == 'score' else replay(folder, args.with_rules)
