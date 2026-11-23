"""#6937: bounded journal capacity receipts at the exact SQLite boundary.

Synthetic metadata only; no physical files, production cleanup or publication.
"""
import sqlite3
import unittest

from cifereca.core.derived_variant_journal import (
    DerivedJournalConflict,
    _COMMITTED,
    _MAX_RECORD_BYTES,
    _MAX_RECORDS,
    _PREFIX,
    _measure,
)


class DerivedJournalCapacityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value BLOB)")
        self.db.execute("BEGIN")

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _insert(self, prefix, count, value="x"):
        self.db.executemany(
            "INSERT INTO metadata(key,value) VALUES (?,?)",
            ((f"{prefix}{index:064x}", value) for index in range(count)),
        )

    def test_exact_record_limit_is_admitted_without_refund(self):
        self._insert(_PREFIX, _MAX_RECORDS)
        rows, total = _measure(self.db)
        self.assertEqual(len(rows), _MAX_RECORDS)
        self.assertEqual(total, _MAX_RECORDS)

    def test_one_more_record_is_rejected_atomically(self):
        self._insert(_PREFIX, _MAX_RECORDS + 1)
        with self.assertRaisesRegex(DerivedJournalConflict, "derived_journal_capacity_exceeded"):
            _measure(self.db)

    def test_pending_and_committed_share_the_same_limit(self):
        self._insert(_PREFIX, _MAX_RECORDS - 1)
        self._insert(_COMMITTED, 2)
        with self.assertRaisesRegex(DerivedJournalConflict, "derived_journal_capacity_exceeded"):
            _measure(self.db)

    def test_oversize_receipt_is_corruption_not_quota_refund(self):
        self._insert(_COMMITTED, 1, "x" * (_MAX_RECORD_BYTES + 1))
        with self.assertRaisesRegex(DerivedJournalConflict, "derived_journal_corrupt"):
            _measure(self.db)

    def test_empty_receipt_is_corruption_not_free_slot(self):
        self._insert(_PREFIX, 1, "")
        with self.assertRaisesRegex(DerivedJournalConflict, "derived_journal_corrupt"):
            _measure(self.db)


if __name__ == "__main__":
    unittest.main()
