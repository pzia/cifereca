"""#6937: corrupted Unicode private retirement records fail closed."""

from pathlib import Path

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_private_retirement as retirement
from cifereca.core import derived_variant_retirement_preparation as preparation
from tests.test_core_p0_6937_private_admission import rig


@pytest.mark.parametrize("kind", ["preparation", "retirement"])
def test_nonascii_sqlite_trace_is_rejected_without_unicode_leak_or_mutation(rig, kind):
    storage, intent, stage, _options = rig
    prefix = preparation._PREFIX if kind == "preparation" else retirement._PREFIX
    key = prefix + "a" * 64
    invalid = "non-canonical-\u00e9"
    before = stage.read_bytes()
    parent = Path(intent.parent_path)
    parent_before = parent.read_bytes()
    storage.metadata_set(key, invalid)

    with storage._lock:
        db = storage._connect()
        assert not db.in_transaction
        with pytest.raises(journal.DerivedJournalConflict):
            (preparation._measure(db) if kind == "preparation"
             else retirement._trace_snapshot(db))
        assert not db.in_transaction
        assert db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone() == (invalid,)

    assert storage.metadata_get(key) == invalid
    assert stage.read_bytes() == before
    assert parent.read_bytes() == parent_before
