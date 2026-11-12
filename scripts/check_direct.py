"""Check the code-only rules against train answers. Uses no model."""
from collections import Counter
import json
from pathlib import Path
import sys

from datamate.direct import Direct
from datamate.loading import open_business
from datamate.metrics import Metrics
from datamate.scoring import outcome

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return [json.loads(l) for l in (ROOT / 'train' / name).read_text(encoding='utf-8-sig').splitlines()]


answers = {r['id']: r for r in load('answers.jsonl')}
engines, tally, wrong = {}, Counter(), []
for question in load('questions.jsonl'):
    business = question['business_id']
    if business not in engines:
        data, failure = open_business(ROOT / 'train', business, Path('.cache/metrics'))
        engines[business] = Direct(Metrics(data)) if not failure else None
    if engines[business] is None:
        continue
    found = engines[business].answer(question)
    half = 'even' if int(business.split('_')[1]) % 2 == 0 else 'odd'
    if found is None:
        tally[half, 'declined'] += 1
        continue
    result = outcome({'id': question['id'], **found}, answers[question['id']])
    tally[half, result] += 1
    if result != 'correct':
        wrong.append((half, question['id'], question['question'][:120], str(answers[question['id']].get('answer'))[:40], str(found.get('answer'))[:40]))
print(sorted(tally.items()))
# Rules are written from the odd shops; even shops stay unread as the check.
for row in [r for r in wrong if r[0] == 'odd'][:int(sys.argv[1]) if len(sys.argv) > 1 else 30]:
    print(row)
