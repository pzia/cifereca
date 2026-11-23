"""#6937: fail-closed quota inspection with SQLite dynamic typing."""
import sqlite3
import unittest

from cifereca.core.derived_variant_journal import (
    DerivedJournalConflict, _COMMITTED, _PREFIX, _measure,
)


class JournalSqliteTypeGuardsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value BLOB)")
        self.db.execute("BEGIN")

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_null_receipt_is_not_a_free_quota_slot(self):
        self.db.execute(
            "INSERT INTO metadata(key,value) VALUES (?,NULL)",
            (_PREFIX + "0" * 64,),
        )
        with self.assertRaisesRegex(DerivedJournalConflict, "derived_journal_corrupt"):
            _measure(self.db)

    def test_blob_receipt_is_not_silently_accepted_as_text(self):
        # Size-only preflight cannot prove a BLOB is a valid JSON receipt.
        # The subsequent receipt decoder must reject it before admission.
        from cifereca.core.derived_variant_journal import _decode
        key = _COMMITTED + "0" * 64
        self.db.execute("INSERT INTO metadata(key,value) VALUES (?,?)", (key, b"{}"))
        rows, _ = _measure(self.db)
        self.assertEqual(len(rows), 1)
        with self.assertRaisesRegex(DerivedJournalConflict, "derived_journal_corrupt"):
            _decode(key, self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0])

    def test_valid_sized_receipt_is_charged_even_before_decoding(self):
        self.db.execute("INSERT INTO metadata(key,value) VALUES (?,?)",
                        (_PREFIX + "1" * 64, "not-json"))
        rows, total = _measure(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(total, len("not-json"))


if __name__ == "__main__":
    unittest.main()
