"""#6937: compact proof admission has an independent byte budget."""
import sqlite3
import unittest

from cifereca.core.derived_variant_compact_receipt import (
    _PREFIX, _MAX_PROOF_BYTES, _measure_compact_in_transaction,
)
from cifereca.core.derived_variant_journal import DerivedJournalConflict


class CompactProofByteBudgetTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value BLOB)")
        self.db.execute("BEGIN")

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_oversize_compact_payload_is_refused(self):
        self.db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                        (_PREFIX + "a" * 64, "x" * (_MAX_PROOF_BYTES + 1)))
        with self.assertRaises(DerivedJournalConflict):
            _measure_compact_in_transaction(self.db)

    def test_null_compact_payload_is_refused(self):
        self.db.execute("INSERT INTO metadata(key,value) VALUES(?,NULL)",
                        (_PREFIX + "b" * 64,))
        with self.assertRaises(DerivedJournalConflict):
            _measure_compact_in_transaction(self.db)

    def test_zero_byte_compact_payload_is_refused(self):
        self.db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                        (_PREFIX + "c" * 64, ""))
        with self.assertRaises(DerivedJournalConflict):
            _measure_compact_in_transaction(self.db)


if __name__ == "__main__":
    unittest.main()
