"""#6937: compact witness namespace cannot silently expand or refund quota."""
import sqlite3
import unittest

from cifereca.core.derived_variant_compact_receipt import (
    _PREFIX,
    _MAX_PROOFS,
    _measure_compact_in_transaction,
)
from cifereca.core.derived_variant_journal import DerivedJournalConflict


class CompactWitnessCapacityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value BLOB)")

    def tearDown(self):
        self.db.close()

    def test_snapshot_required_even_when_namespace_empty(self):
        with self.assertRaisesRegex(DerivedJournalConflict, "derived_compact_snapshot_required"):
            _measure_compact_in_transaction(self.db)

    def test_empty_namespace_is_bounded_and_valid(self):
        self.db.execute("BEGIN")
        self.assertEqual(_measure_compact_in_transaction(self.db), (0, 0))

    def test_one_more_than_witness_capacity_fails_before_decoding(self):
        self.db.executemany(
            "INSERT INTO metadata(key,value) VALUES (?,?)",
            ((f"{_PREFIX}{i:064x}", "invalid") for i in range(_MAX_PROOFS + 1)),
        )
        self.db.commit()
        self.db.execute("BEGIN")
        with self.assertRaisesRegex(DerivedJournalConflict, "derived_compact_capacity_exceeded"):
            _measure_compact_in_transaction(self.db)

    def test_malformed_witness_cannot_be_charged_as_valid(self):
        self.db.execute(
            "INSERT INTO metadata(key,value) VALUES (?,?)",
            (f"{_PREFIX}{0:064x}", "[]"),
        )
        self.db.commit()
        self.db.execute("BEGIN")
        with self.assertRaises(DerivedJournalConflict):
            _measure_compact_in_transaction(self.db)


if __name__ == "__main__":
    unittest.main()
