"""Which code rules the training split backs, and the switch that keeps only those.

Some rules were written after reading public-test questions (repair shop, phone shop, installments, baby-shop fees). They
never answer a training question, so nothing measures them. `DATAMATE_RULES=train` switches off every rule that took part
in answering fewer than `MIN_TRAIN_HITS` training questions; the count comes from `rule_provenance.json`, which
`python -m scripts.rule_census --data <train> --write` builds from the training split alone. `DATAMATE_RULES=all`
(the default) keeps every rule.

A rule switched off still reads the question. If it would have answered, the question abstains (`Withheld`): it does
not fall through to a later rule or the model, since those were never measured on that family either. On public, falling
through turned two book answers into a wrong "not recorded".
"""
import json
import os
from pathlib import Path

PROVENANCE = Path(__file__).with_name('rule_provenance.json')
MIN_TRAIN_HITS = 5

LAYERS = {
    'direct': ['opinion', 'outside_data', 'price_change', 'buy_price', 'series', 'best_day', 'best_month', 'rulebook_superlative', 'drug',
               'product_units', 'product_revenue', 'days_over', 'orders_over', 'customers_over', 'items_over', 'units_total', 'top_items',
               'debt_age', 'top_debtors', 'customer_debt', 'debtor_count', 'plain_total', 'revenue_growth', 'purchases_spoken', 'months_named',
               'group_totals', 'two_values', 'top_customer', 'same_days_growth', 'unsold', 'not_recorded'],
    'common': ['cancelled', 'average', 'shift_gap', 'debt_total', 'debt_collected', 'filtered', 'top_customers', 'sized', 'group_money',
               'tier_money', 'price_on_day', 'rounds', 'split_orders', 'delivered', 'due_orders'],
    'books': ['repair_jobs', 'book_debts', 'phone_averages', 'phone_warranty_sold', 'milk_brand', 'repair', 'installments', 'phone_warranty',
              'trade_in', 'customer_pays', 'phones', 'fees', 'sizes', 'new_members'],
    'ledgers': ['gross_profit', 'staff', 'supplier', 'tet', 'expiry', 'import_minus_sold', 'stock_value', 'out_of_stock', 'stock_left'],
}


def scope():
    value = os.environ.get('DATAMATE_RULES', 'all').strip().lower()
    if value not in {'all', 'train'}:
        raise ValueError(f'DATAMATE_RULES must be all or train, not {value!r}')
    return value


def unbacked(layer):
    """Rule names of `layer` that the training split does not back; empty when every rule is kept."""
    if scope() == 'all':
        return []
    if not PROVENANCE.exists():
        raise FileNotFoundError(f'{PROVENANCE.name} missing; run python -m scripts.rule_census --data <train> --write')
    record = json.loads(PROVENANCE.read_text(encoding='utf-8'))
    least = record.get('min_train_hits', MIN_TRAIN_HITS)
    # A rule missing from the file was added after the census, so no training question backs it yet.
    return [name for name in LAYERS[layer] if record['rules'].get(f'{layer}.{name}', {}).get('used', 0) < least]


class Withheld(Exception):
    """An unbacked rule recognised the question; the whole question abstains."""


def switch_off(obj, layer):
    """Make each unbacked rule of `obj` withhold the question it would have answered, and decline the rest."""
    for name in unbacked(layer):
        method = getattr(obj, name, None)
        if method is None:
            continue

        def withhold(*args, _method=method, _name=f'{layer}.{name}', **kwargs):
            if _method(*args, **kwargs) is not None:
                raise Withheld(_name)
            return None
        setattr(obj, name, withhold)
    return obj
