"""#6937: reserve terminal retirement trace capacity before unlink."""
import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_private_retirement as retire
from tests.test_core_p0_6937_private_admission import rig
from tests.test_core_p0_6937_private_retirement import (
    isolated_fence, _published, _prepare, _unchanged,
)


def test_prepared_trace_reserves_full_terminal_capacity(rig, isolated_fence, monkeypatch):
    db, intent, stage, options = _published(rig)
    quota, content = journal.summarize_derived_journal(db), stage.read_bytes()
    digest = options["receipt_sha256"]
    key = retire._PREFIX + digest
    monkeypatch.setattr(retire, "_MAX_TOTAL_BYTES", retire._MAX_RECORD_BYTES - 1)
    with pytest.raises(journal.DerivedJournalConflict, match="derived_retirement_capacity"):
        retire.prepare_private_stage_retirement(db, **options)
    assert db.metadata_get(key) is None and stage.samefile(intent.candidate_path)
    _unchanged(db, intent, quota, digest, content)

    monkeypatch.setattr(retire, "_MAX_TOTAL_BYTES", retire._MAX_RECORD_BYTES)
    approved = _prepare(db, options)
    raw = db.metadata_get(key)
    assert len(raw.encode("ascii")) < retire._MAX_RECORD_BYTES
    with db._lock, db._connect() as sql:
        _, reserved = retire._trace_snapshot(sql)
        assert reserved == retire._MAX_RECORD_BYTES
    # Even if the capacity policy were changed in the interim, refuse before
    # unlink rather than discover terminal trace saturation afterwards.
    monkeypatch.setattr(retire, "_MAX_TOTAL_BYTES", retire._MAX_RECORD_BYTES - 1)
    with pytest.raises(journal.DerivedJournalConflict, match="derived_retirement_capacity"):
        retire.retire_private_stage(db, **approved)
    assert db.metadata_get(key) == raw and stage.samefile(intent.candidate_path)
    _unchanged(db, intent, quota, digest, content)

    monkeypatch.setattr(retire, "_MAX_TOTAL_BYTES", retire._MAX_RECORD_BYTES)
    result = retire.retire_private_stage(db, **approved)
    assert result["status"] == "retired" and not result["quota_refund_authorized"]
    assert not stage.exists()
    final = db.metadata_get(key)
    with db._lock, db._connect() as sql:
        _, measured = retire._trace_snapshot(sql)
        assert measured == len(final.encode("ascii"))
    _unchanged(db, intent, quota, digest, content)
