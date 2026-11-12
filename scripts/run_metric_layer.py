"""The model fills metric slots and fixed code computes the answer; questions outside the layer abstain."""
import argparse
import hashlib
import json
import os
from contextlib import ExitStack
from pathlib import Path
import time

from datamate.rulebook_check import Guard
from datamate.direct import Direct
from datamate.loading import date_hint, load_questions, open_business
from datamate.metrics import Metrics, SLOT_REQUEST, Unsupported, check_evidence
from datamate.model import serve
from datamate.provenance import scope as rules_scope
from datamate.records import load_json, read_jsonl, write_json
from datamate.rewrite import rewrite
from datamate.sql_fallback import try_sql_fallback

ROOT = Path(__file__).resolve().parents[1]


guard = None
# Off sends every question to the model, to measure it on shop types the rules never saw.
rules_on = True
# On, a question the rules decline is restated in plain words by the model and read again.
rewrite_on = True
# On, questions outside both rules and the slot form attempt dual-verified read-only SQL synthesis.
sql_fallback_on = True
# A second model, of another family, that writes the SQL fallback's prompt B; None reuses the main model.
verifier = None


def watch_rulebook(text):
    """Make later answers decline questions whose rulebook line lost a term the code relies on."""
    global guard
    guard = Guard(text)


def answer(question, engine, model):
    started = time.monotonic()
    record = {**question, 'status': 'completed'}
    final = {'id': question['id'], 'type': 'abstain'}
    if guard is not None and guard.blocks(question):
        return {**record, 'status': 'out_of_layer', 'reason': 'rulebook line changed', 'final_answer': final, 'elapsed_seconds': round(time.monotonic() - started, 4)}
    try:
        # Rule books load raw tables once per shop, so one reader serves all its questions.
        if not hasattr(engine, 'direct'):
            engine.direct = Direct(engine)
        direct = engine.direct.answer(question) if rules_on else None
        asked = question
        if direct is None and rewrite_on and model is not None:
            # Rules and checks read words; the plain wording gives them a second chance at what shorthand hid.
            try:
                text, record['rewrite_output'] = rewrite(model, question)
            except Exception:
                text = None
            record['rewrite'] = text
            if text:
                asked = {**question, 'question': text}
                direct = engine.direct.answer(asked) if rules_on else None
        if direct is not None:
            final = {'id': question['id'], **direct}
            if final['type'] == 'abstain':
                # A withheld question (datamate/provenance.py) keeps its reason in the log, not in the submission.
                record['reason'] = final.pop('reason', None)
            return {**record, 'slots': {'metric': 'direct'}, 'final_answer': final, 'elapsed_seconds': round(time.monotonic() - started, 4)}
        messages = [{'role': 'system', 'content': SLOT_REQUEST + '\nRECORDED LABELS:\n' + json.dumps(engine.labels(), ensure_ascii=False)},
                    {'role': 'user', 'content': 'Today is ' + question['as_of_date'] + '.\nQUESTION: ' + asked['question'] + date_hint(asked)}]
        # The trained adapter, when loaded, fills the form; the rewrite above stays on the base model.
        raw, usage, finish = model.chat(messages, 120, max_tokens=300, lora=True)
        record.update(model_output=raw, usage=usage, finish_reason=finish)
        slots = load_json(raw)
        record['slots'] = slots
        check_evidence(slots, asked['question'])
        final = {'id': question['id'], **engine.compute(slots, question['as_of_date'])}
    except Unsupported as exc:
        record.update(status='out_of_layer', reason=str(exc))
    except Exception as exc:
        record.update(status='slot_error', reason=type(exc).__name__ + ': ' + str(exc))
    if final['type'] == 'abstain' and sql_fallback_on and model is not None:
        asked_q = locals().get('asked', question)
        fb_ans, fb_meta = try_sql_fallback(question, asked_q['question'], engine, model, date_hint(asked_q), verifier)
        if fb_meta:
            record['sql_fallback'] = fb_meta
        if fb_ans is not None:
            final = {'id': question['id'], **fb_ans}
            record['status'] = 'completed'
            record['slots'] = {'metric': 'sql_fallback'}
            record.pop('reason', None)
    return {**record, 'final_answer': final, 'elapsed_seconds': round(time.monotonic() - started, 4)}


def run(args):
    global rules_on, rewrite_on, sql_fallback_on, verifier
    if args.rules_scope:
        os.environ['DATAMATE_RULES'] = args.rules_scope
    rules_on, rewrite_on = args.rules == 'on', args.rewrite == 'on'
    sql_fallback_on = getattr(args, 'sql_fallback', 'on') == 'on'
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / 'logs').mkdir(exist_ok=True)
    data_root = Path(args.data).resolve()
    # A separate questions file lets the fair test ask about a split's shops in other words.
    source = Path(args.questions).resolve() if args.questions else data_root / 'questions.jsonl'
    questions = sorted(read_jsonl(source), key=lambda q: q['business_id']) if args.questions else load_questions(args.question_set, data_root)
    watch_rulebook((data_root / 'rulebook.md').read_text(encoding='utf-8-sig'))
    config = {'method': 'metric-layer', 'question_ids': sorted(q['id'] for q in questions),
              'questions_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'out_of_layer': 'abstain', 'rules': args.rules, 'rewrite': args.rewrite,
              'sql_fallback': getattr(args, 'sql_fallback', 'on'),
              'prompt_sha256': hashlib.sha256(SLOT_REQUEST.encode()).hexdigest(),
              'lora': args.lora, 'rules_scope': rules_scope(), 'verifier_model': args.verifier_model}
    write_json(output / 'run.json', {'state': 'preparing', 'config': config})
    started = time.monotonic()
    for lora in ([args.lora, None] if args.lora else [None]):
        try:
            with ExitStack() as stack:
                model, metadata = stack.enter_context(serve(args.model, args.runtime, output, 24576, 8, int(os.environ.get('GPU_LAYERS', 99)), lora=lora))
                verifier = start_verifier(stack, args, output, config)
                answer_all(questions, data_root, args.databases, output, model)
            break
        except (FileNotFoundError, RuntimeError, OSError, ValueError) as exc:
            if lora:
                # A broken adapter must not cost the base model's answers.
                print('adapter unavailable, retrying with the base model alone:', type(exc).__name__, exc, flush=True)
                config['lora'] = None
                continue
            # Code rules need no model, so a model that will not start must not blank the whole test.
            print('model unavailable, answering by code rules only:', type(exc).__name__, exc, flush=True)
            metadata = {'unavailable': str(exc)}
            answer_all(questions, data_root, args.databases, output, None)
    write_json(output / 'run.json', {'state': 'complete', 'config': config, 'model': metadata,
               'elapsed_seconds': round(time.monotonic() - started, 3), 'answered_records': len(questions)})


def start_verifier(stack, args, output, config):
    """The second model for the SQL fallback's prompt B, or None. One that will not start falls back to the main model."""
    if not args.verifier_model or not sql_fallback_on:
        return None
    try:
        second, _ = stack.enter_context(serve(args.verifier_model, args.runtime, output, 16384, 8, int(os.environ.get('GPU_LAYERS', 99)),
                                              log_name='verifier-server.log'))
        second.name = Path(args.verifier_model).name
        return second
    except (FileNotFoundError, RuntimeError, OSError, ValueError, TimeoutError) as exc:
        print('verifier unavailable, prompt B stays on the main model:', type(exc).__name__, exc, flush=True)
        config['verifier_model'] = None
        return None


def answer_all(questions, data_root, databases, output, model):
    with (output / 'submission.jsonl').open('w') as stream:
        engines = {}
        for index, question in enumerate(questions, 1):
            business = question['business_id']
            if business not in engines:
                # A shop that cannot be loaded leaves its questions blank; the rest still run.
                try:
                    data, failure = open_business(data_root, business, databases)
                    engines[business] = Metrics(data) if not failure else None
                except Exception as exc:
                    failure, engines[business] = type(exc).__name__ + ': ' + str(exc), None
                if failure:
                    print(business, 'unavailable:', failure, flush=True)
            if engines[business] is None:
                record = {**question, 'status': 'business_unavailable', 'final_answer': {'id': question['id'], 'type': 'abstain'}, 'elapsed_seconds': 0}
            else:
                record = answer(question, engines[business], model)
            write_json(output / 'logs' / (hashlib.sha256(question['id'].encode()).hexdigest() + '.json'), record)
            stream.write(json.dumps(record['final_answer'], ensure_ascii=False) + '\n')
            stream.flush()
            print(f'{index}/{len(questions)} {record["status"]} {record["elapsed_seconds"]:.1f}s', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--data', default=str(ROOT / 'train'))
    parser.add_argument('--model', required=True)
    parser.add_argument('--runtime', default='.runtime-cuda')
    parser.add_argument('--databases', default='.cache/metrics')
    parser.add_argument('--question-set', default='all')
    parser.add_argument('--rules', choices=['on', 'off'], default='on')
    parser.add_argument('--rewrite', choices=['on', 'off'], default='on')
    parser.add_argument('--sql-fallback', choices=['on', 'off'], default='on')
    parser.add_argument('--questions', help='questions file to use instead of the split\'s own')
    parser.add_argument('--lora', help='trained slot-filling adapter (GGUF), applied to the form calls only')
    parser.add_argument('--verifier-model', help='second GGUF model, of another family, for the SQL fallback prompt B')
    parser.add_argument('--rules-scope', choices=['all', 'train'], help='train keeps only rules the training split backs (default: DATAMATE_RULES or all)')
    run(parser.parse_args())
