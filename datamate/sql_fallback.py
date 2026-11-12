"""Dual-verified constrained SQL fallback for tail and unseen-domain questions.

Runs only when deterministic rules and the slot-filling metric layer both decline.
Uses the already-loaded base Qwen3-14B model (lora=False, zero extra parameters) with
two independent SQL synthesis prompts over the read-only normalized + raw SQLite databases.
An answer is emitted only when both independent SQL queries execute cleanly, pass Rulebook
guards, and produce identical normalized answers; otherwise the system safely abstains (0 pts).
"""
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from datamate.metrics import money, standard_months, two
from datamate.normalized import name_key
from datamate.records import load_json
from datamate.scoring import outcome

NORMALIZED_DDL = """Normalized SQLite tables & views:
- VIEW sales(order_id, line_id, product_id, sale_unit, quantity, basic_quantity, unit_price, basic_unit_price, line_revenue, lineage, order_date, status, customer_id, customer_name, customer_group, channel, payment_method, product_name, product_group, base_unit, prescription, is_service)
  NOTE: `sales` ALREADY filters `orders.status IN ('completed', 'shipping')` (excludes cancelled and returned orders per Rulebook §1). Always prefer `sales` for revenue, order counts, item quantities, customer spend, and rankings!
- VIEW daily_revenue(order_date, revenue, order_count) -- pre-aggregated from `sales`
- orders(order_id, order_date, created_at, status, customer_id, payment_method, invoice_total, lineage, channel)
  status in ('completed', 'shipping', 'cancelled', 'returned'); payment_method in ('cash', 'transfer', 'ewallet', 'cod', 'debt')
- order_extras(order_id, debt_left, table_name, area, guest_count)
- order_lines(order_id, line_id, product_id, sale_unit, quantity, basic_quantity, unit_price, basic_unit_price, line_revenue, lineage)
- products(product_id, product_name, product_group, base_unit, prescription, is_service, active_ingredient)
- product_aliases(product_id, alias)
- customers(customer_id, customer_name, customer_group)
- payments(payment_id, order_id, payment_date, amount, lineage)
- purchase_lines(import_id, import_date, product_id, purchase_unit, quantity, unit_price, recorded_total, source_status, lineage)
- stock_report(product_id, on_hand, unit)
- inventory_lots(lot_id, product_id, import_date, imported_quantity, lineage)
- lot_sales(order_id, lot_id, product_id, sold_quantity, returned_quantity)
- unit_conversions(product_id, sale_unit, factor, base_unit) -- 1 sale_unit = factor base_unit"""

PROMPT_A = """You are a precision SQLite analyst for Vietnamese retail businesses (Zalo AI Challenge DataMate).
Write ONE read-only SQLite SELECT/WITH query to answer the user's question strictly according to the Rulebook, or abstain if uncertain.

CRITICAL RULEBOOK INVARIANTS:
1. Today is `as_of_date`. For relative periods ("hôm nay", "tháng này", "tuần trước", "từ đầu năm"), records after `as_of_date` have not happened yet (`order_date <= as_of_date`).
2. Revenue & valid order counts (`sales` view): ONLY orders with `status IN ('completed', 'shipping')` count. Cancelled (`cancelled`) and returned (`returned`, even partial return) orders are EXCLUDED (`sales` view already enforces this). Service fees (`is_service = 1`, e.g. Phí phục vụ) count in revenue, NOT in product rankings/counts.
3. Cancel rate: `100.0 * SUM(status = 'cancelled') / COUNT(*)` over `orders` in the period (includes all statuses).
4. Rankings ("bán chạy nhất"): rank by `SUM(basic_quantity)` DESC (NOT revenue unless money/doanh thu is asked), excluding `is_service = 1`. Tie-break by `product_id` ASC (or `customer_id` ASC).
5. Debt (`còn nợ` at date D): `status != 'cancelled'`, `COALESCE(payment_method,'') != 'cod'`, `COALESCE(LOWER(channel),'') NOT IN ('shopee','lazada','tiktok','tiki','san_tmdt','marketplace')`. Returned orders STILL count if unpaid > 0.
6. Custom raw sheets (if attached under `raw."<table>"`): use ONLY when the question asks about domain-specific columns not in normalized tables (e.g. repair jobs, warranty, rooms, tables, staff, delivery rounds). Exclude `__dm_*` metadata columns.
7. Output JSON ONLY:
   - If confident: `{"abstain": false, "answer_type": "number|percent|string|date|boolean|list|table", "is_money": true|false, "columns": ["col1", ...], "sql": "SELECT ..."}`
   - If the question is ambiguous, subjective, outside data, or cannot be answered with 100% certainty: `{"abstain": true}`"""

PROMPT_B = """You are an independent Rulebook auditor and SQLite query verifier for Vietnamese business data.
First verify the exact Rulebook conditions required by the question, then write ONE self-contained SQLite SELECT/WITH query.

CHECKLIST BEFORE WRITING SQL:
- `period_check`: Exact `[start_date, end_date]` in `YYYY-MM-DD` anchored to `as_of_date`. Week starts Monday. "Tháng này" = 1st of month to `as_of_date`. "N ngày gần nhất" = `[as_of_date - (N-1) days, as_of_date]`.
- `status_check`: Use `sales` view for revenue/orders/quantities/customers. Never include `cancelled` or `returned` orders in sales metrics. Exclude `COALESCE(is_service, 0) = 1` when counting/ranking products.
- `format_check`:
  * `number`: single scalar number (set `"is_money": true` if VND currency or average money so it rounds to integer VND; `"is_money": false` for counts/quantities/non-money averages).
  * `percent`: single percentage on 0..100 scale (e.g. `12.34` for 12.34%, NOT `0.1234`).
  * `string`: exact accented name from the database.
  * `date`: `"YYYY-MM-DD"` or `"YYYY-MM"` (when asking which month).
  * `boolean`: `1` or `0` for yes/no questions.
  * `list`: ordered 1-column rows (or 1 row with multiple values in the exact order asked).
  * `table`: multi-column rows matching `"columns"`, first column = entity name, remaining = metrics in asked order.
- If ANY step is uncertain, subjective, or missing from the schema, return `{"abstain": true}`.

Return JSON ONLY:
`{"period_check": "...", "status_check": "...", "abstain": false, "answer_type": "number|percent|string|date|boolean|list|table", "is_money": true|false, "columns": [...], "sql": "SELECT ..."}`"""


def _relevant_products(engine, question_text, limit=60):
    rows = engine.rows('SELECT product_id, product_name, product_group, base_unit FROM products')
    if len(rows) <= limit:
        return rows
    q_words = set(re.findall(r'\w+', name_key(question_text, accents=True)))
    scored = []
    for r in rows:
        p_words = set(re.findall(r'\w+', name_key(r[1] or '', accents=True)))
        scored.append((len(q_words & p_words), r))
    scored.sort(key=lambda x: (-x[0], str(x[1][0])))
    return [r for _, r in scored[:limit]]


def _raw_tables_summary(engine, question_text):
    raw = getattr(engine.data, 'raw', None)
    if raw is None or not getattr(raw, 'path', None) or not Path(raw.path).is_file():
        return ''
    want_cal = bool(re.search(r'tet|am lich|lich am|ram|mung', name_key(question_text, accents=True)))
    lines = ['Attached `raw` database tables (query as `raw."<table>"`):']
    try:
        conn = sqlite3.connect(f'file:{raw.path}?mode=ro', uri=True)
        try:
            for meta in getattr(raw, 'tables', []):
                tname = meta.get('table')
                if not tname or (tname == 'calendar' and not want_cal):
                    continue
                cols = [c for c in meta.get('columns', []) if not str(c).startswith('__dm_')]
                if not cols:
                    continue
                col_list = ', '.join(f'"{c}"' for c in cols[:18])
                sample = conn.execute(f'SELECT {col_list} FROM "{tname}" LIMIT 1').fetchone()
                sample_str = json.dumps(list(sample), ensure_ascii=False)[:180] if sample else '[]'
                lines.append(f'- raw."{tname}"({col_list}) sample={sample_str}')
        finally:
            conn.close()
    except sqlite3.Error:
        return ''
    return '\n'.join(lines) if len(lines) > 1 else ''


def build_schema_prompt(engine, question_text):
    if not hasattr(engine, '_sql_static_meta'):
        labels = engine.labels()
        caps = sorted(engine.capabilities)
        readme = ''
        raw = getattr(engine.data, 'raw', None)
        if raw is not None and getattr(raw, 'readme', None):
            readme = str(raw.readme).strip()[:1000]
        engine._sql_static_meta = (labels, caps, readme)
    labels, caps, readme = engine._sql_static_meta
    prods = _relevant_products(engine, question_text)
    prod_lines = ', '.join(f'{r[1]} [{r[3] or "-"}]' for r in prods[:50])
    parts = [
        NORMALIZED_DDL,
        f'Shop data date span: {engine.first} to {engine.last}',
        f'Recorded capabilities: {json.dumps(caps, ensure_ascii=False)}',
        f'Recorded filter labels: {json.dumps(labels, ensure_ascii=False)}',
        f'Catalogue products (name [base_unit]): {prod_lines}',
    ]
    raw_summary = _raw_tables_summary(engine, question_text)
    if raw_summary:
        parts.append(raw_summary)
    if readme:
        parts.append('Shop README notes:\n' + readme)
    return '\n\n'.join(parts)


def clean_sql(sql):
    if not isinstance(sql, str):
        return None
    text = sql.strip()
    text = re.sub(r'^```(?:sql)?\s*|\s*```$', '', text, flags=re.I).strip()
    text = re.sub(r';+\s*$', '', text).strip()
    if not text or ';' in text:
        return None
    bare = re.sub(r'^\s*(?:--[^\n]*\n\s*)+', '', text)
    if not re.match(r'^(?:SELECT|WITH)\b', bare, flags=re.I):
        return None
    if re.search(r'\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REPLACE)\b', bare, flags=re.I):
        return None
    return text


def sql_passes_guards(sql, question_text):
    plain = name_key(question_text, accents=True)
    low = sql.lower()
    # Direct queries on `orders` or `order_lines` (without `sales`) for revenue/order count must mention status,
    # unless asking about cancel/return/debt/single-day price.
    if ('from orders' in low or 'join orders' in low or 'from order_lines' in low) and 'sales' not in low:
        if not re.search(r'status|huy|tra|no\b|debt|payment|gia\b', plain + ' ' + low):
            return False
    return True


def execute_readonly(engine, sql, timeout=3.0, max_rows=200):
    data_path = getattr(engine.data, 'path', None)
    if data_path and Path(data_path).is_file():
        conn = sqlite3.connect(f'file:{data_path}?mode=ro', uri=True)
        try:
            raw = getattr(engine.data, 'raw', None)
            if raw is not None and getattr(raw, 'path', None) and Path(raw.path).is_file():
                conn.execute('ATTACH DATABASE ? AS raw', [f'file:{raw.path}?mode=ro'])

            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
            conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
            deadline = time.monotonic() + timeout
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)
            cur = conn.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            rows = [list(r) for r in cur.fetchmany(max_rows + 1)]
            if len(rows) > max_rows:
                return None, None
            return cols, rows
        finally:
            conn.close()
    res = engine.data.query(sql, [], timeout=max(1, int(timeout)), max_rows=max_rows)
    rows = res.get('rows', [])
    cols = res.get('columns') or [f'c{i}' for i in range(len(rows[0]) if rows else 0)]
    return cols, rows


def _format_scalar_number(val, is_money):
    if isinstance(val, bool) or val is None:
        return None
    fval = float(val)
    if not math.isfinite(fval):
        return None
    if is_money:
        return money(fval)
    if abs(fval - round(fval)) < 1e-9:
        return int(round(fval))
    return two(fval)


def format_sql_result(spec, cols, rows):
    atype = spec.get('answer_type')
    is_money = bool(spec.get('is_money', False))
    # No tables: the three the fallback gave on the held-out fair shops were all wrong (a sum asked "for every drink"
    # came back as one row per drink), and the agreement check cannot catch a shape both prompts get wrong.
    if not rows or atype not in {'number', 'percent', 'string', 'date', 'boolean', 'list'}:
        return None
    if atype == 'number':
        if len(rows) != 1 or len(rows[0]) != 1:
            return None
        num = _format_scalar_number(rows[0][0], is_money)
        # A zero is what COUNT returns when a wrong filter matches nothing, and both prompts make the same
        # mistake, so the agreement check cannot catch it (9 of 10 zeros were wrong on public_test).
        return {'type': 'number', 'answer': num} if num else None
    if atype == 'percent':
        if len(rows) != 1 or len(rows[0]) != 1 or rows[0][0] is None or isinstance(rows[0][0], bool):
            return None
        fval = float(rows[0][0])
        if not math.isfinite(fval) or fval < -100.0 or fval > 10000.0 or fval == 0:
            return None
        return {'type': 'percent', 'answer': two(fval)}
    if atype == 'string':
        if len(rows) != 1 or len(rows[0]) != 1 or not isinstance(rows[0][0], str):
            return None
        sval = rows[0][0].strip()
        if not sval or sval.replace('.', '', 1).lstrip('-').isdigit():
            return None
        if re.match(r'^\d{4}-\d{2}(?:-\d{2})?$', sval):
            # "Which month sold most?" answered with strftime('%Y-%m') is a date, whatever type the prompt named.
            return {'type': 'date', 'answer': sval}
        return {'type': 'string', 'answer': sval}
    if atype == 'date':
        if len(rows) != 1 or len(rows[0]) != 1 or rows[0][0] is None:
            return None
        dval = str(rows[0][0]).strip()
        if not re.match(r'^\d{4}-\d{2}(?:-\d{2})?$', dval):
            return None
        return {'type': 'date', 'answer': dval}
    if atype == 'boolean':
        if len(rows) != 1 or len(rows[0]) != 1 or rows[0][0] is None:
            return None
        raw_v = rows[0][0]
        if isinstance(raw_v, bool):
            bval = raw_v
        elif isinstance(raw_v, (int, float)) and raw_v in (0, 1):
            bval = bool(raw_v)
        elif str(raw_v).strip().lower() in ('true', '1', 'yes', 'co'):
            bval = True
        elif str(raw_v).strip().lower() in ('false', '0', 'no', 'khong'):
            bval = False
        else:
            return None
        return {'type': 'boolean', 'answer': bval}
    if atype == 'list':
        if len(rows) == 1 and len(rows[0]) >= 2:
            raw_items = rows[0]
        elif len(rows) >= 1 and len(rows[0]) == 1:
            raw_items = [r[0] for r in rows]
        else:
            return None
        out = []
        for item in raw_items:
            if item is None:
                return None
            if isinstance(item, str):
                s = item.strip()
                if not s:
                    return None
                out.append(s)
            else:
                num = _format_scalar_number(item, is_money)
                if num is None:
                    return None
                out.append(num)
        return {'type': 'list', 'answer': out} if out else None
    return None


def _run_one_prompt(system_prompt, user_prompt, question_text, engine, model, temperature=0, seed=None):
    raw, usage, _ = model.chat(
        [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}],
        timeout=90,
        temperature=temperature,
        seed=seed,
        max_tokens=500,
        lora=False,
    )
    spec = load_json(raw)
    if not isinstance(spec, dict) or spec.get('abstain') is True or 'sql' not in spec:
        return None, {'raw': raw, 'usage': usage, 'stage': 'declined'}
    sql = clean_sql(spec.get('sql'))
    if not sql or not sql_passes_guards(sql, question_text):
        return None, {'raw': raw, 'usage': usage, 'stage': 'sql_rejected'}
    cols, rows = execute_readonly(engine, sql)
    if cols is None:
        return None, {'raw': raw, 'usage': usage, 'sql': sql, 'stage': 'too_many_rows'}
    formatted = format_sql_result(spec, cols, rows)
    if formatted is None:
        return None, {'raw': raw, 'usage': usage, 'sql': sql, 'stage': 'empty_or_bad_format'}
    return formatted, {'raw': raw, 'usage': usage, 'sql': sql, 'stage': 'ok'}


def try_sql_fallback(question, asked_text, engine, model, date_hint_str='', verifier=None):
    """Attempt dual-verified SQL synthesis; return (answer_dict, meta_dict) or (None, meta_dict).

    Prompt B runs on `verifier` when one is given: a model of another family, so the two queries do not share one model's
    habits. Without it, prompt B runs on the same model at a slightly different temperature and seed.
    """
    if model is None:
        return None, {}
    try:
        schema_info = build_schema_prompt(engine, asked_text)
        orig = question['question']
        q_block = f'Today (`as_of_date`): {question["as_of_date"]}\nQUESTION: {asked_text}'
        if asked_text != orig:
            q_block += f'\nORIGINAL WORDING: {orig}'
        if date_hint_str:
            q_block += date_hint_str
        user_prompt = schema_info + '\n\n' + q_block

        ans_a, meta_a = _run_one_prompt(PROMPT_A, user_prompt, asked_text, engine, model, temperature=0, seed=2026)
        if ans_a is None:
            return None, {'sql_fallback_a': meta_a}

        if verifier is not None:
            ans_b, meta_b = _run_one_prompt(PROMPT_B, user_prompt, asked_text, engine, verifier, temperature=0, seed=2027)
            meta_b['model'] = getattr(verifier, 'name', 'verifier')
        else:
            ans_b, meta_b = _run_one_prompt(PROMPT_B, user_prompt, asked_text, engine, model, temperature=0.2, seed=2027)
        if ans_b is None:
            return None, {'sql_fallback_a': meta_a, 'sql_fallback_b': meta_b}

        if outcome({'id': question['id'], **ans_a}, {'id': question['id'], **ans_b}) == 'correct':
            return ans_a, {'sql_fallback_a': meta_a, 'sql_fallback_b': meta_b, 'verified': True}
        return None, {'sql_fallback_a': meta_a, 'sql_fallback_b': meta_b, 'verified': False, 'disagreed': [ans_a, ans_b]}
    except Exception as exc:
        return None, {'sql_fallback_error': f'{type(exc).__name__}: {exc}'}
