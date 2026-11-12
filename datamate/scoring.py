"""Local implementation of the published scorer, not the organizers' code."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import math
import unicodedata

from .records import valid_answer, number


def normalize(value):
    value = unicodedata.normalize("NFC", value).lower()
    return " ".join("".join(c if c.isalnum() or c.isspace() else " " for c in value).split())


def same_scalar(prediction, reference, aliases=()):
    if isinstance(reference, str):
        return isinstance(prediction, str) and normalize(prediction) in {
            normalize(v) for v in [reference, *aliases] if isinstance(v, str)
        }
    if not number(prediction) or not number(reference):
        return False
    if Decimal(str(prediction)) == Decimal(str(reference)):
        return True
    # shortcut: only sub-nanounit floating noise is tolerated; replace with official scorer when available.
    return math.isclose(prediction, reference, rel_tol=0, abs_tol=1e-9)


def unique_rows(rows):
    seen = set()
    result = []
    for index, row in enumerate(rows):
        key = tuple(("s", normalize(v)) if isinstance(v, str) else ("n", Decimal(str(v))) for v in row)
        if key not in seen:
            seen.add(key)
            result.append((index, row))
    return result


def same_table(prediction, reference, aliases):
    pc = [normalize(c) for c in prediction["columns"]]
    rc = [normalize(c) for c in reference["columns"]]
    if len(pc) != len(rc) or len(set(pc)) != len(pc) or len(set(rc)) != len(rc):
        return False
    order = [pc.index(c) for c in rc] if set(pc) == set(rc) else list(range(len(rc)))
    predicted = unique_rows([[row[i] for i in order] for row in prediction["rows"]])
    expected = unique_rows(reference["rows"])
    if len(predicted) != len(expected):
        return False

    def matches(p, original_index, r):
        names = aliases[original_index] if original_index < len(aliases) else []
        return all(same_scalar(p[i], value, names if i == 0 else ()) for i, value in enumerate(r))

    edges = [[j for j, (ri, r) in enumerate(expected) if matches(p, ri, r)] for _, p in predicted]
    assigned = {}

    def assign(i, visited):
        for j in edges[i]:
            if j not in visited:
                visited.add(j)
                if j not in assigned or assign(assigned[j], visited):
                    assigned[j] = i
                    return True
        return False

    return all(assign(i, set()) for i in range(len(predicted)))


def outcome(prediction, reference):
    if prediction is None or prediction.get("type") == "abstain":
        return "abstain"
    # The split rulebooks explicitly treat non-string reason fields as unreadable/abstain.
    if "reason" in prediction and not isinstance(prediction["reason"], str):
        return "abstain"
    if not valid_answer(prediction) or prediction["type"] != reference["type"]:
        return "wrong"
    kind = reference["type"]
    p, r = prediction.get("answer"), reference.get("answer")
    aliases = reference.get("alias", [])
    if kind == "unanswerable":
        correct = True
    elif kind == "boolean":
        correct = p is r
    elif kind == "date":
        correct = p == r
    elif kind == "percent":
        # shortcut: halfway rounding is not specified publicly; use decimal half-up locally.
        correct = Decimal(str(p)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal(str(r)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    elif kind == "list":
        correct = len(p) == len(r) and all(
            same_scalar(p[i], value, aliases[i] if i < len(aliases) else ()) for i, value in enumerate(r)
        )
    elif kind == "table":
        correct = same_table(p, r, aliases)
    else:
        correct = same_scalar(p, r, aliases)
    return "correct" if correct else "wrong"


def metrics(rows):
    counts = {key: sum(r["outcome"] == key for r in rows) for key in ("correct", "wrong", "abstain")}
    n = len(rows)
    answered = counts["correct"] + counts["wrong"]
    return {"questions": n, **counts, "pa": (counts["correct"] - counts["wrong"]) / n if n else 0,
            "coverage": answered / n if n else 0,
            "accuracy_when_answered": counts["correct"] / answered if answered else None}


def evaluate(questions, references, predictions):
    qmap = {q["id"]: q for q in questions}
    gold = {r["id"]: r for r in references}
    given = {p["id"]: p for p in predictions}
    if len(qmap) != len(questions) or len(gold) != len(references) or len(given) != len(predictions):
        raise ValueError("Duplicate question/reference/prediction id")
    if set(qmap) - set(gold):
        raise ValueError("Missing reference answers for evaluation questions")
    if set(given) - set(qmap):
        raise ValueError("Submission contains IDs outside this evaluation set")
    rows = [{"id": q["id"], "business_id": q["business_id"], "question": q["question"],
             "reference": gold[q["id"]], "prediction": given.get(q["id"]),
             "outcome": outcome(given.get(q["id"]), gold[q["id"]])} for q in questions]
    groups = {"by_business": defaultdict(list), "by_type": defaultdict(list)}
    for row in rows:
        groups["by_business"][row["business_id"]].append(row)
        groups["by_type"][row["reference"]["type"]].append(row)
    report = {**metrics(rows), **{name: {key: metrics(value) for key, value in group.items()} for name, group in groups.items()}}
    return report, rows
