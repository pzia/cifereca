"""#6937: separate compact witness capacity is observable without cleanup authority."""
import sqlite3
import threading
import unittest
from cifereca.core.derived_variant_compact_receipt import review_compact_capacity, _MAX_PROOFS, _MAX_PROOF_TOTAL

class _Storage:
    def __init__(self):
        self._lock = threading.RLock()
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        self.db.commit()
    def _connect(self):
        return self.db

class CompactCapacityHeadroomTests(unittest.TestCase):
    def test_empty_compact_store_reports_full_headroom(self):
        storage = _Storage()
        try:
            report = review_compact_capacity(storage)
            self.assertEqual(report["remaining_record_slots"], _MAX_PROOFS)
            self.assertEqual(report["remaining_payload_bytes"], _MAX_PROOF_TOTAL)
            self.assertFalse(report["admission_saturated"])
            self.assertFalse(report["destructive_cleanup_allowed"])
            self.assertFalse(report["quota_refund_authorized"])
        finally:
            storage.db.close()

if __name__ == "__main__":
    unittest.main()
