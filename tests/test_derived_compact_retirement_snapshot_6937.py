"""#6937: retirement witness inspection requires a SQL snapshot."""
import sqlite3
import unittest
from cifereca.core.derived_variant_compact_receipt import _retired_receipts
from cifereca.core.derived_variant_journal import DerivedJournalConflict

class RetirementSnapshotTests(unittest.TestCase):
    def test_retirement_scan_requires_transaction(self):
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value BLOB)")
            with self.assertRaises(DerivedJournalConflict):
                _retired_receipts(db)
            db.execute("BEGIN")
            self.assertEqual(_retired_receipts(db), {})
        finally:
            db.close()

if __name__ == "__main__":
    unittest.main()
