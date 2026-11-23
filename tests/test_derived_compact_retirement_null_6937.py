"""#6937: retired markers cannot be absent, NULL, or unpaired."""
import sqlite3
import unittest
from cifereca.core.derived_variant_compact_receipt import _RETIRED_PREFIX, _retired_receipts
from cifereca.core.derived_variant_journal import DerivedJournalConflict

class RetirementNullTests(unittest.TestCase):
    def test_null_marker_does_not_refund_quota(self):
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value BLOB)")
            db.execute("INSERT INTO metadata(key,value) VALUES (?,NULL)",
                       (_RETIRED_PREFIX + "a" * 64,))
            db.commit()
            db.execute("BEGIN")
            with self.assertRaises(DerivedJournalConflict):
                _retired_receipts(db)
        finally:
            db.close()

if __name__ == "__main__":
    unittest.main()
