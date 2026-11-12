"""Loss-preserving imports and bounded read-only SQL execution."""
import csv
from datetime import date, datetime
from functools import cached_property
import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

from openpyxl import load_workbook


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def numeric(value, decimal="."):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"(?:VND|VNĐ|đ|₫)$", "", text, flags=re.I)
    if decimal == ",":
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        result = float(text)
        if result != result or abs(result) == float("inf"):
            return None
        return result
    except ValueError:
        return None


def iso_date(value, year=None):
    if value is None:
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(text.split()[0], pattern).date().isoformat()
        except (ValueError, IndexError):
            pass
    if year is not None and re.fullmatch(r"\d{1,2}[/-]\d{1,2}", text):
        try:
            day, month = re.split(r"[/-]", text)
            return date(int(year), int(month), int(day)).isoformat()
        except (ValueError, TypeError):
            pass
    return None


def text_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value) if value is not None else None


def table_name(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_") or "data"


class BusinessData:
    def __init__(self, split, business_id, cache):
        self.split = Path(split).resolve()
        self.directory = (self.split / "data" / business_id).resolve()
        if self.directory.parent != self.split / "data" or not self.directory.is_dir():
            raise ValueError("Business ID must identify one directory under split/data")
        self.files = sorted(self.directory.iterdir()) + [self.split / "calendar.csv"]
        self.readme = (self.directory / "README.md").read_text(encoding="utf-8-sig")
        fingerprint = hashlib.sha256()
        fingerprint.update(b"datamate-import-v2")
        for path in self.files:
            if path.is_file():
                fingerprint.update(f"{path}:{path.stat().st_size}:{path.stat().st_mtime_ns}".encode())
        self.cache = Path(cache).resolve() / fingerprint.hexdigest()[:24]
        self.cache.mkdir(parents=True, exist_ok=True)
        self.path = self.cache / "data.sqlite"
        self.meta_path = self.cache / "schema.json"
        if not self.path.exists() or not self.meta_path.exists():
            self.build()
        self.tables = json.loads(self.meta_path.read_text())

    def build(self):
        # One temporary file per process, so two runs sharing a cache never rename each other's half-built file.
        temp = self.path.with_suffix(f".building.{os.getpid()}.sqlite")
        temp.unlink(missing_ok=True)
        connection = sqlite3.connect(temp)
        connection.execute("PRAGMA journal_mode=OFF")
        tables = []
        used_names = set()

        def add_table(stem, header, rows, source, sheet=""):
            header = [str(v).strip() if v is not None else f"column_{i + 1}" for i, v in enumerate(header)]
            unique = []
            for value in header:
                candidate = "source" + value if value.startswith("__dm_") else value or "column"
                while candidate.casefold() in {x.casefold() for x in unique}:
                    candidate += "_"
                unique.append(candidate)
            name = table_name(stem)
            while name in used_names:
                name += "_"
            used_names.add(name)
            columns = unique + ["__dm_file", "__dm_sheet", "__dm_row"]
            connection.execute(f"CREATE TABLE {quote(name)} ({', '.join(quote(c) + ' TEXT' for c in columns)})")
            insert = f"INSERT INTO {quote(name)} VALUES ({','.join('?' for _ in columns)})"
            count = 0
            batch = []
            for row_number, row, row_sheet in rows:
                if not any(v is not None and v != "" for v in row):
                    continue
                if len(row) > len(unique) and any(v is not None and v != "" for v in row[len(unique):]):
                    raise ValueError(f"{source}/{row_sheet}:{row_number}: values beyond detected header")
                values = list(row[:len(unique)])
                values += [None] * (len(unique) - len(values))
                batch.append([text_value(v) for v in values] + [source, row_sheet, str(row_number)])
                count += 1
                if len(batch) == 2000:
                    connection.executemany(insert, batch)
                    batch.clear()
            connection.executemany(insert, batch)
            tables.append({"table": name, "source": source, "sheet": sheet, "columns": columns, "rows": count})

        try:
            for path in self.files:
                suffix = path.suffix.lower()
                if suffix == ".csv":
                    with path.open(encoding="utf-8-sig", newline="") as stream:
                        reader = csv.reader(stream, strict=True)
                        header = next(reader)
                        add_table(path.stem, header, ((i, row, "") for i, row in enumerate(reader, 2)), path.name)
                elif suffix == ".xlsx":
                    workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
                    try:
                        # Sheets with identical headers in one workbook form one table; provenance is retained.
                        groups = {}
                        for sheet in workbook:
                            iterator = sheet.iter_rows(values_only=True)
                            preamble = []
                            for i, row in enumerate(iterator, 1):
                                preamble.append((i, row))
                                if i == 20:
                                    break
                            populated = [(i, row) for i, row in preamble if sum(v is not None and v != "" for v in row) >= 2]
                            if not populated:
                                raise ValueError(f"No tabular header found: {path.name}/{sheet.title}")
                            # shortcut: first full-width textual row is the header; inspect/revise for new export layouts.
                            header_row, header = next(((i, row) for i, row in populated
                                if all(v is None or isinstance(v, str) for v in row)
                                and sum(v is not None and v != "" for v in row) == max(sum(v is not None and v != "" for v in r) for _, r in populated)), populated[0])
                            header = list(header)
                            while header and header[-1] is None:
                                header.pop()
                            groups.setdefault(tuple(header), []).append((sheet.title, header_row))
                        for index, (header, sheets) in enumerate(groups.items()):
                            def rows(sheets=sheets):
                                for title, header_row in sheets:
                                    for i, row in enumerate(workbook[title].iter_rows(min_row=header_row + 1, values_only=True), header_row + 1):
                                        yield i, row, title
                            stem = path.stem if len(groups) == 1 else f"{path.stem}_{sheets[0][0]}"
                            add_table(stem, header, rows(), path.name, ", ".join(title for title, _ in sheets))
                    finally:
                        workbook.close()
                elif suffix == ".sqlite":
                    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
                    try:
                        names = [r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                        for name in names:
                            cursor = source.execute(f"SELECT * FROM {quote(name)}")
                            add_table(f"{path.stem}_{name}", [col[0] for col in cursor.description],
                                      ((i, row, name) for i, row in enumerate(cursor, 1)), path.name, name)
                    finally:
                        source.close()
            connection.commit()
            connection.close()
            temp.replace(self.path)
            self.meta_path.write_text(json.dumps(tables, ensure_ascii=False, indent=2))
        except BaseException:
            connection.close()
            temp.unlink(missing_ok=True)
            raise

    def query(self, sql, params=(), timeout=10, max_rows=100):
        if not isinstance(sql, str) or len(sql) > 30000 or not isinstance(params, list | tuple):
            raise ValueError("Query must be SQL text with a parameter list")
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True)
        # Unknown quoted identifiers must fail instead of becoming string constants.
        connection.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DML, False)
        connection.execute("PRAGMA query_only=ON")
        connection.enable_load_extension(False)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1000000)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 30000)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        connection.create_function("num", 1, numeric, deterministic=True)
        connection.create_function("num", 2, numeric, deterministic=True)
        connection.create_function("iso_date", 1, iso_date, deterministic=True)
        connection.create_function("iso_date", 2, iso_date, deterministic=True)
        connection.create_function("vn_lower", 1, lambda v: str(v).lower() if v is not None else None, deterministic=True)
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

        def authorize(action, first, second, database, trigger):
            if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() in {"load_extension", "readfile", "writefile"}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

        connection.set_authorizer(authorize)
        deadline = time.monotonic() + timeout
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            cursor = connection.execute(sql, params)
            if cursor.description is None:
                raise ValueError("Only read-only SELECT queries are permitted")
            rows = cursor.fetchmany(max_rows + 1)
            result = {"columns": [c[0] for c in cursor.description], "rows": [list(row) for row in rows[:max_rows]],
                      "truncated": len(rows) > max_rows}
            if len(json.dumps(result, ensure_ascii=False, allow_nan=False)) > 20000:
                raise ValueError("Query output too large; aggregate or select fewer columns")
            return result
        finally:
            connection.close()

    def inspect(self, name):
        if name not in {t["table"] for t in self.tables}:
            raise ValueError("Unknown table")
        return self.query(f"SELECT * FROM {quote(name)} LIMIT 3")

    @cached_property
    def value_profile(self):
        profiles = []
        for table in self.tables:
            if table['table'] == 'calendar':
                continue
            for column in table['columns']:
                name = table_name(column)
                is_status = any(key in name for key in ('trang_thai', 'tinh_trang', 'status', 'ghi_chu_tt'))
                is_date = bool(set(name.split('_')) & {'ngay', 'date', 'time'}) or 'thoi_gian' in name
                if not is_status and not is_date:
                    continue
                entry = {'table': table['table'], 'column': column, 'kind': 'status' if is_status else 'date'}
                value, source = quote(column), quote(table['table'])
                limit = 20 if is_status else 3
                try:
                    result = self.query(
                        f'SELECT {value}, COUNT(*) FROM {source} '
                        f'WHERE {value} IS NOT NULL AND trim({value}) != \'\' '
                        f'GROUP BY {value} ORDER BY COUNT(*) DESC, {value} LIMIT {limit + 1}', max_rows=limit)
                    entry['truncated'] = result['truncated']
                    if is_status:
                        entry['values'] = [{'raw': raw, 'rows': count} for raw, count in result['rows']]
                    else:
                        entry['examples'] = [{'raw': raw, 'iso_date': iso_date(raw)} for raw, _ in result['rows']]
                        entry['sql_expression'] = f'iso_date({value})'
                except (sqlite3.Error, ValueError) as exc:
                    entry['error'] = str(exc)
                profiles.append(entry)
        return profiles
