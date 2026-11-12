"""Re-judge saved model slots from a public run with the current rules and checks, and write a layer-only file. Uses no model."""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import tarfile

from datamate.direct import Direct
from datamate.metrics import Metrics, Unsupported, check_evidence
from datamate.loading import open_business

ROOT = Path(__file__).resolve().parents[1]


def saved_slots(record):
    layer = record.get('metric_layer') or record
    return layer.get('slots')


def main(args):
    run = Path(args.run)
    records = {}
    with tarfile.open(next(run.rglob('traces.tar.gz'))) as archive:
        for member in archive:
            if member.isfile() and member.name.endswith('.json'):
                record = json.load(archive.extractfile(member))
                if 'final_answer' in record:
                    records[record['id']] = record
    questions = [json.loads(l) for l in (ROOT / 'public_test/questions.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    engines, tally, changed = {}, Counter(), []
    with Path(args.output).open('w', encoding='utf-8') as stream:
        for question in questions:
            business = question['business_id']
            if business not in engines:
                data, failure = open_business(ROOT / 'public_test', business, Path('.cache/metrics-public'))
                if failure:
                    raise ValueError(business + ': ' + failure)
                engines[business] = Direct(Metrics(data))
            direct, record = engines[business], records[question['id']]
            final = {'id': question['id'], 'type': 'abstain'}
            try:
                found = direct.answer(question)
                slots = copy.deepcopy(saved_slots(record))
                if found is None and isinstance(slots, dict) and slots.get('metric') != 'direct':
                    check_evidence(slots, question['question'])
                    found = direct.metrics.compute(slots, question['as_of_date'])
                if found is not None:
                    final = {'id': question['id'], **found}
            except Unsupported:
                pass
            except Exception as exc:
                tally['error ' + type(exc).__name__] += 1
            stream.write(json.dumps(final, ensure_ascii=False) + '\n')
            before = record['final_answer'] if not record.get('steps') else {'type': 'abstain'}
            same = {k: before.get(k) for k in ('type', 'answer')} == {k: final.get(k) for k in ('type', 'answer')}
            tally['answered' if final['type'] != 'abstain' else 'blank'] += 1
            tally['same' if same else 'new' if before['type'] == 'abstain' else 'dropped' if final['type'] == 'abstain' else 'changed'] += 1
            if not same:
                changed.append((question['question'][:80], str(before.get('answer'))[:22], str(final.get('answer'))[:22]))
    print(dict(tally))
    for row in changed[:args.show]:
        print(row)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run')
    parser.add_argument('output')
    parser.add_argument('--show', type=int, default=30)
    main(parser.parse_args())
