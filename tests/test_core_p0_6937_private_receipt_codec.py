"""#6937: versioned reader compatibility, not live staging admission."""
from dataclasses import asdict, replace
from pathlib import Path
import json

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_compact_receipt as compact
from cifereca.core import derived_variant_region_review as region
from cifereca.core.derived_variant_region_paths import PRIVATE_DERIVED_REGION
from tests.test_core_p0_4520_derived_journal import _fixture


def _v3(intent, **changes):
    body = {"schema": journal._PRIVATE_SCHEMA, "nonce": "f" * 32,
            "intent": asdict(intent), "region_binding_sha256": "e" * 64}
    body.update(changes)
    return journal._encode(body)


def _private(intent):
    parent = Path(intent.parent_path)
    return replace(intent, staged_path=str(parent.parent / PRIVATE_DERIVED_REGION / "stage.epub"),
                   options_fingerprint="private")


def test_mixed_versions_are_counted_in_same_quota_and_writer_stays_closed(tmp_path):
    service, legacy = _fixture(tmp_path)
    db = service.storage
    try:
        prior = journal.reserve_derived_publication(db, legacy)
        old = db.metadata_get(prior.key)
        private = _private(legacy)
        private = replace(private, candidate_path=str(Path(legacy.candidate_path).with_name("private.epub")))
        raw = _v3(private)
        key = journal._slot(private)
        # Synthetic historical record only: the legacy writer must never
        # be able to introduce or consume a v3 receipt.
        with pytest.raises(journal.DerivedJournalConflict):
            journal.reserve_derived_publication(db, private)
        db.metadata_set(key, raw)
        assert journal._decode(key, raw) == private
        info = journal.summarize_derived_journal(db)
        assert info["pending_count"] == 2 and info["reserved_output_bytes"] == 18
        assert info["payload_bytes"] == len(old) + len(raw)
        assert db.metadata_get(prior.key) == old
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            assert journal._read_pending_reservation(sql, prior)[1] == legacy
            with pytest.raises(journal.DerivedJournalConflict, match="private_region_review_required"):
                journal._read_pending_reservation(sql, journal.DerivedReservation(key, journal._receipt_digest(raw)))
        assert db.metadata_get(key) == raw and len(db.placements()) == 1
    finally:
        db.close()


@pytest.mark.parametrize("bad", ["legacy_path", "missing_binding", "bad_binding", "hidden_stage",
                                 "outside_profile", "legacy_disguise", "noncanonical"])
def test_schema_path_and_binding_are_not_interchangeable(tmp_path, bad):
    service, intent = _fixture(tmp_path)
    try:
        private = _private(intent)
        raw = _v3(private)
        if bad == "legacy_path":
            raw = _v3(intent)
        elif bad == "missing_binding":
            body = json.loads(raw); del body["region_binding_sha256"]; raw = journal._encode(body)
        elif bad == "bad_binding":
            raw = _v3(private, region_binding_sha256="not-a-digest")
        elif bad == "hidden_stage":
            raw = _v3(replace(private, staged_path=str(Path(private.staged_path).with_name(".core-owner-v1"))))
        elif bad == "outside_profile":
            raw = _v3(replace(private, staged_path=str(tmp_path / "other" / PRIVATE_DERIVED_REGION / "file.epub")))
        elif bad == "legacy_disguise":
            raw = journal._encode({"schema": journal._LEGACY_SCHEMA, "nonce": "f" * 32, "intent": asdict(private)})
        else:
            raw = json.dumps(json.loads(raw), indent=1)
        with pytest.raises(journal.DerivedJournalConflict, match="corrupt"):
            journal._decode(journal._COMMITTED + journal._receipt_digest(raw), raw)
    finally:
        service.storage.close()


def test_private_compact_round_trip_retains_exact_binding_and_original_digest(tmp_path):
    service, intent = _fixture(tmp_path)
    try:
        raw = _v3(_private(intent))
        key = journal._COMMITTED + journal._receipt_digest(raw)
        proof_key, proof = compact.encode_committed_witness(key, raw)
        assert json.loads(proof)[0] == compact._PRIVATE_COMPACT_SCHEMA
        assert compact.decode_committed_witness(proof_key, proof) == (key, raw)
        modified = json.loads(proof); modified[3] = "1" * 64
        with pytest.raises(journal.DerivedJournalConflict):
            compact.decode_committed_witness(proof_key, journal._encode(modified))
        assert len(proof) <= len(raw)
    finally:
        service.storage.close()


def test_region_review_counts_private_names_without_treating_marker_as_stage(tmp_path):
    service, intent = _fixture(tmp_path)
    db = service.storage
    private = _private(intent)
    stage = Path(private.staged_path)
    try:
        stage.parent.mkdir(mode=0o700)
        stage.write_bytes(b"new bytes")
        (stage.parent / ".core-owner-v1").write_bytes(b"f" * 32)
        info = stage.stat()
        private = replace(private, output_stamp=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
        db.metadata_set(journal._slot(private), _v3(private))
        report = region.review_derived_private_region(db, library_root=tmp_path,
                                                       identity_id="id1", explicitly_requested=True)
        assert report["counts"]["pending_named"] == 1
        assert report["counts"]["unrecorded_regular"] == 0
        assert not report["exclusive_region_verified"] and not report["retention_authorized"]
        assert stage.read_bytes() == b"new bytes"
    finally:
        db.close()
