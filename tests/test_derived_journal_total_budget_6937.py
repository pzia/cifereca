"""#6937: aggregate receipt byte budget is checked before publication."""
import sqlite3
import unittest
from cifereca.core.derived_variant_journal import (
    DerivedJournalConflict, _MAX_RECORDS, _MAX_RECORD_BYTES,
    _MAX_TOTAL_BYTES, _PREFIX, _measure,
)

class JournalTotalBudgetTests(unittest.TestCase):
    def test_full_byte_budget_exact_boundary(self):
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value BLOB)")
            db.executemany(
                "INSERT INTO metadata(key,value) VALUES (?,?)",
                ((f"{_PREFIX}{i:064x}", "x" * _MAX_RECORD_BYTES)
                 for i in range(_MAX_RECORDS)),
            )
            db.commit()
            db.execute("BEGIN")
            rows, total = _measure(db)
            self.assertEqual(len(rows), _MAX_RECORDS)
            self.assertEqual(total, _MAX_TOTAL_BYTES)
        finally:
            db.close()

if __name__ == "__main__":
    unittest.main()
