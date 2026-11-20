"""#6937: compact traces are a distinct budget, never a free journal slot."""
import json
from pathlib import Path

import pytest

from cifereca.core import derived_variant_compact_receipt as compact
from cifereca.core import derived_variant_capacity_review as review
from cifereca.core import derived_variant_journal as journal
from cifereca.core.derived_variant_maintenance_review import admit_maintenance_review
from cifereca.gui.derived_variant_capacity_line import _subtitle
from tests.test_core_p0_4520_derived_journal import _publish_reserved
from tests.test_core_p0_4520_recovery_operator import _setup


def _read(service):
    return review.read_capacity_review(service, admit_maintenance_review(service))


@pytest.mark.parametrize("case", ["empty", "paired", "unpaired", "corrupt", "at_limit", "over_limit"])
def test_compact_budget_independent_and_closed(tmp_path, monkeypatch, case):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        key = journal._COMMITTED + reservation.receipt_sha256
        original = db.metadata_get(key)
        before = journal.summarize_derived_journal(db)
        raw = original
        source = key
        if case == "unpaired":
            body = json.loads(original)
            body["nonce"] = "f" * 32 if body["nonce"] != "f" * 32 else "e" * 32
            raw = journal._encode(body)
            source = journal._COMMITTED + journal._receipt_digest(raw)
        pk, proof = compact.encode_committed_witness(source, raw)
        if case != "empty":
            db.metadata_set(pk, proof if case != "corrupt" else "unreadable")
        if case == "at_limit":
            monkeypatch.setattr(compact, "_MAX_PROOFS", 1)
        elif case == "over_limit":
            monkeypatch.setattr(compact, "_MAX_PROOF_TOTAL", len(proof) - 1)
        result = _read(service)
        assert result is not None
        report = result["compact"]
        assert not report["retirement_authorized"] and not report["quota_refund_authorized"]
        text = _subtitle(result)
        if case in ("corrupt", "over_limit"):
            assert result["status"] == "partial" and report["status"] == "unavailable"
            assert report["count"] is None and report["saturation"] == "unknown"
            assert "capacité indéterminée" in text
        else:
            assert report["count"] == (0 if case == "empty" else 1)
            assert report["matched"] == (1 if case in ("paired", "at_limit") else 0)
            assert report["unpaired"] == (1 if case == "unpaired" else 0)
            assert report["status"] == ("partial" if case == "unpaired" else "observed")
            assert report["saturation"] == ("at_limit" if case == "at_limit" else "below_limits")
            assert f'{report["matched"]} rattachés' in text
            assert f'{report["unpaired"]} non rattachés' in text
            if case == "unpaired":
                assert result["status"] == "partial"
        assert "Traces compactes" in text and "Aucun quota libéré" in text
        assert str(tmp_path) not in repr(result) and pk not in repr(result)
        assert result["record_count"] == before["count"]
        assert result["known_output_bytes"] == before["reserved_output_bytes"]
        assert journal.summarize_derived_journal(db) == before
        assert db.metadata_get(key) == original
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


def test_changed_compact_snapshot_is_not_presented(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        key = journal._COMMITTED + reservation.receipt_sha256
        pk, proof = compact.encode_committed_witness(key, db.metadata_get(key))
        real = review.review_derived_staging_stock

        def changed(*args, **kwargs):
            report = real(*args, **kwargs)
            db.metadata_set(pk, proof)
            return report

        monkeypatch.setattr(review, "review_derived_staging_stock", changed)
        assert _read(service) is None
        assert db.metadata_get(pk) == proof and db.metadata_get(key) is not None
    finally:
        db.close()
