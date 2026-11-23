"""#6937: malformed retirement markers never reclaim journal capacity."""
import sqlite3
import unittest

from cifereca.core.derived_variant_compact_receipt import (
    _RETIRED_PREFIX, _retired_receipts,
)
from cifereca.core.derived_variant_journal import DerivedJournalConflict


class RetirementMarkerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value BLOB)")
        self.db.execute("BEGIN")

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_empty_retirement_inventory(self):
        self.assertEqual(_retired_receipts(self.db), {})

    def test_short_retirement_marker_fails_closed(self):
        self.db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                        (_RETIRED_PREFIX + "a" * 64, "short"))
        with self.assertRaisesRegex(DerivedJournalConflict, "derived_compact_retirement_unverified"):
            _retired_receipts(self.db)

    def test_exact_size_marker_without_witness_fails_closed(self):
        self.db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                        (_RETIRED_PREFIX + "b" * 64, "c" * 64))
        with self.assertRaises(DerivedJournalConflict):
            _retired_receipts(self.db)

    def test_non_digest_key_fails_closed(self):
        self.db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                        (_RETIRED_PREFIX + "not-a-digest", "c" * 64))
        with self.assertRaisesRegex(DerivedJournalConflict, "derived_compact_retirement_unverified"):
            _retired_receipts(self.db)


if __name__ == "__main__":
    unittest.main()
