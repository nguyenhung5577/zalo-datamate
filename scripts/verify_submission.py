"""Validate a competition submission.jsonl against questions.jsonl and optionally diff against a baseline."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

from datamate.records import read_jsonl
from datamate.scoring import outcome

VALID_TYPES = {'number', 'string', 'boolean', 'date', 'percent', 'list', 'table', 'unanswerable', 'abstain'}


def validate_record(rec):
    if not isinstance(rec, dict) or 'id' not in rec or 'type' not in rec:
        return 'missing id or type'
    kind = rec['type']
    if kind not in VALID_TYPES:
        return f'invalid type: {kind!r}'
    if kind in ('unanswerable', 'abstain'):
        return None
    if 'answer' not in rec:
        return f'type {kind} missing answer field'
    ans = rec['answer']
    if kind in ('number', 'percent'):
        if isinstance(ans, bool) or not isinstance(ans, (int, float)) or not math.isfinite(ans):
            return f'invalid numeric answer: {ans!r}'
    elif kind == 'boolean':
        if not isinstance(ans, bool):
            return f'invalid boolean answer: {ans!r}'
    elif kind == 'string':
        if not isinstance(ans, str) or not ans.strip():
            return f'invalid string answer: {ans!r}'
    elif kind == 'date':
        if not isinstance(ans, str) or not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', ans):
            return f'invalid ISO date answer: {ans!r}'
    elif kind == 'list':
        if not isinstance(ans, list) or not ans:
            return f'invalid list answer: {ans!r}'
    elif kind == 'table':
        if not isinstance(ans, dict) or not isinstance(ans.get('columns'), list) or not isinstance(ans.get('rows'), list):
            return f'invalid table answer: {ans!r}'
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('submission', help='path to submission.jsonl')
    parser.add_argument('--questions', required=True, help='path to target questions.jsonl')
    parser.add_argument('--logs', help='optional logs directory to summarize stage breakdown')
    parser.add_argument('--baseline', help='optional baseline submission.jsonl (e.g. submission-v14.jsonl) to diff against')
    args = parser.parse_args()

    sub_rows = list(read_jsonl(Path(args.submission)))
    q_rows = list(read_jsonl(Path(args.questions)))
    expected_ids = [q['id'] for q in q_rows]
    seen_ids = [r.get('id') for r in sub_rows]

    errors = []
    if len(sub_rows) != len(q_rows):
        errors.append(f'row count mismatch: got {len(sub_rows)}, expected {len(q_rows)}')
    if set(seen_ids) != set(expected_ids):
        missing = set(expected_ids) - set(seen_ids)
        extra = set(seen_ids) - set(expected_ids)
        errors.append(f'id set mismatch: missing={len(missing)}, extra={len(extra)}')
    if len(set(seen_ids)) != len(seen_ids):
        errors.append('duplicate question ids present')

    type_counts = Counter()
    for idx, rec in enumerate(sub_rows, 1):
        err = validate_record(rec)
        if err:
            errors.append(f'line {idx} ({rec.get("id")}): {err}')
        else:
            type_counts[rec['type']] += 1

    if errors:
        print('VALIDATION FAILED:')
        for e in errors[:20]:
            print('  -', e)
        raise SystemExit(1)

    print(f'VALIDATION OK: {len(sub_rows)} records match {args.questions}')
    print('Answer types:', dict(sorted(type_counts.items())))

    if args.logs and Path(args.logs).is_dir():
        stages = Counter()
        for rec in sub_rows:
            log_file = Path(args.logs) / (hashlib.sha256(rec['id'].encode()).hexdigest() + '.json')
            if not log_file.is_file():
                stages['missing_log'] += 1
                continue
            log = json.loads(log_file.read_text())
            metric = (log.get('slots') or {}).get('metric')
            if rec['type'] == 'abstain':
                stages['abstain'] += 1
            elif metric == 'direct':
                stages['direct_rules'] += 1
            elif metric == 'sql_fallback':
                stages['sql_fallback'] += 1
            else:
                stages['slot_model'] += 1
        print('Stage breakdown:', dict(stages))

    if args.baseline and Path(args.baseline).is_file():
        base = {r['id']: r for r in read_jsonl(Path(args.baseline))}
        q_map = {q['id']: q for q in q_rows}
        diff_counts = Counter()
        changes = []
        for rec in sub_rows:
            qid = rec['id']
            old = base.get(qid, {'id': qid, 'type': 'abstain'})
            if old.get('type') == 'abstain' and rec['type'] != 'abstain':
                diff_counts['was_abstain_now_answered'] += 1
                changes.append(('GAIN', qid, old, rec))
            elif old.get('type') != 'abstain' and rec['type'] == 'abstain':
                diff_counts['was_answered_now_abstain'] += 1
                changes.append(('ABSTAIN', qid, old, rec))
            elif outcome(rec, old) == 'correct':
                diff_counts['semantically_same'] += 1
            else:
                diff_counts['changed_answer'] += 1
                changes.append(('CHANGED', qid, old, rec))
        print(f'Diff vs {args.baseline}:', dict(diff_counts))
        for tag, qid, old, new in changes:
            biz = q_map.get(qid, {}).get('business_id', '?')
            text = q_map.get(qid, {}).get('question', '')
            print(f'  [{tag}] [{biz}] {qid}: old={old.get("type")}:{old.get("answer")} -> new={new.get("type")}:{new.get("answer")} | {text}')


if __name__ == '__main__':
    main()
