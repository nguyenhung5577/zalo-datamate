"""Count which code rule answers each question, and write the rules the training split backs.

Every rule method of the four layers (direct, common, books, ledgers) is wrapped once. A rule that returns an answer is
recorded, also when another rule called it; the rule that answered the question is the last to return (the outermost).

    python -m scripts.rule_census --data ../train --write          # datamate/rule_provenance.json, from train only
    python -m scripts.rule_census --data ../public_test --out x.json  # where public answers come from (read only)

The provenance file is built from the training split alone: it holds, for each rule, how many train questions it took
part in answering. `DATAMATE_RULES=train` (datamate/provenance.py) switches off every rule below `MIN_TRAIN_HITS`.
Only `--write` with the training split writes it; any other split only reports.
"""
import argparse
from collections import Counter
import functools
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datamate.direct import Direct  # noqa: E402
from datamate.loading import open_business  # noqa: E402
from datamate.metrics import Metrics  # noqa: E402
from datamate.provenance import LAYERS, MIN_TRAIN_HITS, PROVENANCE  # noqa: E402
from datamate.records import read_jsonl  # noqa: E402


def instrument(obj, layer, hits):
    for name in LAYERS[layer]:
        method = getattr(obj, name, None)
        if method is None:
            continue

        @functools.wraps(method)
        def wrapped(*a, _m=method, _n=f'{layer}.{name}', **k):
            found = _m(*a, **k)
            if found is not None:
                hits.append(_n)
            return found
        setattr(obj, name, wrapped)


def census(root, cache):
    root = Path(root)
    from scripts import run_metric_layer as runner
    runner.watch_rulebook((root / 'rulebook.md').read_text(encoding='utf-8-sig'))
    questions = sorted(read_jsonl(root / 'questions.jsonl'), key=lambda q: q['business_id'])
    out, engines, hits = {}, {}, []
    for q in questions:
        b = q['business_id']
        if b not in engines:
            data, failure = open_business(root, b, cache)
            d = Direct(Metrics(data)) if not failure else None
            if d is not None:
                instrument(d, 'direct', hits)
                d.open_books()
                instrument(d.common, 'common', hits)
                instrument(d.books, 'books', hits)
                if d.ledgers is not None:
                    instrument(d.ledgers, 'ledgers', hits)
            engines[b] = d
            print('opened', b, file=sys.stderr, flush=True)
        d = engines[b]
        if d is None:
            continue
        hits.clear()
        try:
            found = d.answer(q)
        except Exception as exc:  # noqa: BLE001
            found, hits[:] = None, [f'error:{type(exc).__name__}']
        out[q['id']] = {'business': b, 'rule': hits[-1] if hits and found is not None else None,
                        'used': sorted(set(hits)) if found is not None else []}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--cache', default=str(ROOT / '.cache' / 'metrics'))
    parser.add_argument('--out', help='per-question census (JSON)')
    parser.add_argument('--write', action='store_true', help='write datamate/rule_provenance.json (training split only)')
    args = parser.parse_args()
    # The census must see every rule, whatever scope the shell asks for.
    os.environ['DATAMATE_RULES'] = 'all'
    result = census(args.data, args.cache)
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False))
    answered = Counter(r['rule'] for r in result.values() if r['rule'])
    used = Counter(name for r in result.values() for name in r['used'])
    print(f'{sum(answered.values())} of {len(result)} answered by rules', file=sys.stderr)
    if args.write:
        if not (Path(args.data) / 'answers.jsonl').exists():
            raise SystemExit('--write needs the training split (the one with answers.jsonl); test splits never shape the provenance')
        names = [f'{layer}.{name}' for layer, methods in LAYERS.items() for name in methods]
        PROVENANCE.write_text(json.dumps({
            'built_from': Path(args.data).resolve().name, 'questions': len(result), 'min_train_hits': MIN_TRAIN_HITS,
            'rules': {n: {'answered': answered[n], 'used': used[n]} for n in names}}, indent=1, ensure_ascii=False) + '\n')
        print('wrote', PROVENANCE, file=sys.stderr)


if __name__ == '__main__':
    main()
