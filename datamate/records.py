"""Strict JSONL input/output and submission value validation."""
from datetime import date
import json
import math
from pathlib import Path
import re

TYPES = {"number", "percent", "string", "date", "boolean", "list", "table", "unanswerable", "abstain"}


def reject_constant(value):
    raise ValueError(f"Non-JSON numeric constant: {value}")


def load_json(text):
    return json.loads(text, parse_constant=reject_constant)


def read_jsonl(path):
    records = []
    seen = set()
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                record = load_json(line)
                if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                    raise ValueError("Record needs a string id")
                if record["id"] in seen:
                    raise ValueError("Duplicate id")
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
            seen.add(record["id"])
            records.append(record)
    return records


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def number(value):
    return type(value) in (int, float) and (type(value) is int or math.isfinite(value))


def scalar(value):
    return isinstance(value, str) or number(value)


def valid_answer(record):
    """Validate values without coercing strings, booleans, or dates into other types."""
    if not isinstance(record, dict) or record.get("type") not in TYPES:
        return False
    if "reason" in record and not isinstance(record["reason"], str):
        return False
    kind, value = record["type"], record.get("answer")
    if kind == "abstain":
        return True
    if kind == "unanswerable":
        return value is None
    if kind in ("number", "percent"):
        return number(value)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return type(value) is bool
    if kind == "date":
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", value):
            return False
        try:
            date.fromisoformat(value + "-01" if len(value) == 7 else value)
            return True
        except ValueError:
            return False
    if kind == "list":
        return isinstance(value, list) and all(scalar(item) for item in value)
    if not isinstance(value, dict) or not isinstance(value.get("columns"), list) or not isinstance(value.get("rows"), list):
        return False
    columns = value["columns"]
    return (all(isinstance(c, str) for c in columns)
            and all(isinstance(row, list) and len(row) == len(columns) and all(scalar(v) for v in row)
                    for row in value["rows"]))
