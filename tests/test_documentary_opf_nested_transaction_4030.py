"""#4030: optional OPF corroboration must never settle a borrowed SQL writer."""
import sqlite3
import threading
import unittest
from cifereca.core.local_origin_documentary_suggestions import _matching_source_url

class _Storage:
    def __init__(self, db):
        self._lock = threading.RLock()
        self.db = db
    def _connect(self):
        return self.db

class DocumentaryNestedTransactionTests(unittest.TestCase):
    def test_borrowed_writer_is_neither_committed_nor_rolled_back(self):
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE TABLE marker(value INTEGER)")
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO marker VALUES(1)")
            self.assertIsNone(_matching_source_url(
                _Storage(db), (object(),), "https://example.invalid/source"))
            self.assertTrue(db.in_transaction)
            self.assertEqual(db.execute("SELECT count(*) FROM marker").fetchone(), (1,))
            db.rollback()
            self.assertEqual(db.execute("SELECT count(*) FROM marker").fetchone(), (0,))
        finally:
            db.close()

if __name__ == "__main__":
    unittest.main()
