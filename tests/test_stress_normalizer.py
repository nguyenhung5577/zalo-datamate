from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from scripts.stress_normalizer import compare, new_type, perturb

COLUMNS = ['Mã đơn', 'Trạng thái', '__dm_file', '__dm_sheet', '__dm_row']


def raw_package(folder):
    db = sqlite3.connect(folder / 'raw.sqlite')
    db.execute('CREATE TABLE "don" (' + ','.join(f'"{c}" TEXT' for c in COLUMNS) + ')')
    db.executemany('INSERT INTO "don" VALUES (?,?,?,?,?)', [('D1', 'Xong', 'a.csv', '', '2'), ('D2', 'Xong', 'a.csv', '', '3')])
    db.commit()
    db.close()
    return SimpleNamespace(path=folder / 'raw.sqlite', cache=folder, readme='## Cột trong `a.csv`\n- **Mã đơn**: mã đơn\n',
                           tables=[{'table': 'don', 'source': 'a.csv', 'sheet': '', 'columns': COLUMNS, 'rows': 2}])


def rows(package):
    db = sqlite3.connect(package.path)
    found = [list(r) for r in db.execute('SELECT * FROM don')], [d[1] for d in db.execute('PRAGMA table_info(don)')]
    db.close()
    return found


class StressNormalizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        (self.folder / 'out').mkdir()
        self.raw = raw_package(self.folder)

    def tearDown(self):
        self.temp.cleanup()

    def test_rename_updates_readme_and_keeps_rows(self):
        changed = perturb(self.raw, 'english_with_readme', self.folder / 'out')
        records, columns = rows(changed)
        self.assertEqual(columns[:2], ['Order ID', 'Status'])
        self.assertEqual(records[0][:2], ['D1', 'Xong'])
        self.assertIn('**Order ID**', changed.readme)

    def test_one_cell_scenario_changes_exactly_one_cell(self):
        records, _ = rows(perturb(self.raw, 'bad_status_cell', self.folder / 'out'))
        self.assertEqual(sum(r[1] == 'Chờ xác nhận' for r in records), 1)

    def test_missing_role_is_not_applicable(self):
        self.assertIsNone(perturb(self.raw, 'bad_payment_cell', self.folder / 'out'))

    def test_compare_flags_lost_capability_as_changed(self):
        base = {'revenue': 100.0, 'orders': 5, 'capabilities': ['a', 'b']}
        self.assertEqual(compare(base, dict(base))[0], 'same')
        self.assertEqual(compare(base, {**base, 'capabilities': ['a']})[0], 'changed')
        self.assertEqual(compare(base, {**base, 'revenue': 100.05})[0], 'minor')

    def test_new_type_package_is_readable(self):
        package = new_type(self.folder / 'out', {'diem': (['Điểm'], [('8',)])})
        self.assertEqual(package.tables[0]['rows'], 1)


if __name__ == '__main__':
    unittest.main()
