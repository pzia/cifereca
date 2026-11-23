"""#6937: capacity diagnostics show headroom without permitting cleanup."""
import sqlite3
import threading
import unittest
from cifereca.core.derived_variant_journal import (
    summarize_derived_journal, _MAX_RECORDS, _MAX_TOTAL_BYTES,
    _MAX_RESERVED_OUTPUT_BYTES,
)

class _Storage:
    def __init__(self):
        self._lock = threading.RLock()
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
        self.db.commit()
    def _connect(self):
        return self.db

class JournalCapacityHeadroomTests(unittest.TestCase):
    def test_empty_journal_has_full_headroom_without_cleanup_permission(self):
        storage = _Storage()
        try:
            summary = summarize_derived_journal(storage)
            self.assertEqual(summary["remaining_record_slots"], _MAX_RECORDS)
            self.assertEqual(summary["remaining_payload_bytes"], _MAX_TOTAL_BYTES)
            self.assertEqual(summary["remaining_declared_output_bytes"], _MAX_RESERVED_OUTPUT_BYTES)
            self.assertFalse(summary["admission_saturated"])
            self.assertFalse(summary["destructive_cleanup_allowed"])
        finally:
            storage.db.close()

if __name__ == "__main__":
    unittest.main()
