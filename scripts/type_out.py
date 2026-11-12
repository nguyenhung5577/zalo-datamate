"""Leave-one-shop-type-out: how the model stage does on a shop type its adapter never saw.

The private test has shop types that are in neither train nor public, and the code rules were written from every train
shop, so no train number measures the rules on a new type. The adapter can be measured: train it with
`scripts/train_slots.py --leave-out <type>`, then run the runner with `--rules off` on that type's questions.

    python -m scripts.type_out prepare nha_thuoc        # results/type_out/nha_thuoc-{clean,noisy,para}.jsonl
    python -m scripts.run_metric_layer --data <train> --questions results/type_out/nha_thuoc-clean.jsonl --rules off \
        --model <gguf> --lora <fold adapter> --output results/type_out/run-nha_thuoc-clean
    python -m scripts.type_out score results/type_out/run-nha_thuoc-*

Shop types are in splits/shop_types.json (from each train shop's README; a trailing ? marks a guess). Penalized
accuracy is (right - wrong) / questions, the leaderboard's formula. Adapted from a teammate's datamate-agent repo.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from datamate.records import read_jsonl
from datamate.scoring import outcome
from scripts.fair_test import TRAIN_DIR, noisy, para

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results' / 'type_out'
TYPES = ROOT / 'splits' / 'shop_types.json'


def shop_types(path=TYPES):
    return {business: label.rstrip('?') for business, label in json.loads(Path(path).read_text(encoding='utf-8')).items()}


def prepare(kind, data=TRAIN_DIR):
    types = shop_types()
    if kind not in set(types.values()):
        raise SystemExit(f'unknown type {kind}; known: {sorted(set(types.values()))}')
    questions = [q for q in read_jsonl(Path(data) / 'questions.jsonl') if types.get(q['business_id']) == kind]
    OUT.mkdir(parents=True, exist_ok=True)
    for name, change in (('clean', lambda q: q['question']), ('noisy', noisy), ('para', para)):
        with (OUT / f'{kind}-{name}.jsonl').open('w') as stream:
            for question in questions:
                stream.write(json.dumps({**question, 'question': change(question)}, ensure_ascii=False) + '\n')
    print(len(questions), f'{kind} questions x 3 sets ->', OUT)


def score(folders, data=TRAIN_DIR):
    gold = {r['id']: r for r in read_jsonl(Path(data) / 'answers.jsonl')}
    for folder in folders:
        tally, stages = Counter(), Counter()
        for found in read_jsonl(Path(folder) / 'submission.jsonl'):
            log = json.loads((Path(folder) / 'logs' / (hashlib.sha256(found['id'].encode()).hexdigest() + '.json')).read_text())
            result = outcome(found, gold[found['id']])
            metric = (log.get('slots') or {}).get('metric')
            stage = 'none' if result == 'abstain' else 'rules' if metric == 'direct' else 'sql_fallback' if metric == 'sql_fallback' else 'model'
            tally[result] += 1
            stages[stage, result] += 1
        n = sum(tally.values())
        print(f'{Path(folder).name}: {n} questions, right {tally["correct"]}, wrong {tally["wrong"]}, abstain {tally["abstain"]}, '
              f'PA {(tally["correct"] - tally["wrong"]) / max(n, 1):.4f} | '
              + ', '.join(f'{s} {stages[s, "correct"]}/{stages[s, "wrong"]}' for s in ('rules', 'model', 'sql_fallback')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('step', choices=['prepare', 'score'])
    parser.add_argument('args', nargs='+', help='prepare: a shop type; score: runner output folders')
    options = parser.parse_args()
    if options.step == 'prepare':
        prepare(options.args[0])
    else:
        score(options.args)
