"""#976: canonical origin evidence is per Paris account, not per SHA."""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.core.adept_canonical_origin_review import (
    review_paris_canonical_adept_links,
)
from cifereca.storage import Storage


SHA = "a" * 64
ALT_SHA = "b" * 64


def _artifact(storage, sha=SHA):
    storage.artifact_put({
        "artifact_id": "sha256:" + sha,
        "sha256": sha, "size": 42, "media_format": "epub",
    })
    return "sha256:" + sha


def _source(db, ref, *, identity="alice", credential="paris-a",
            account="primary", right="paris-loan", item="item-1",
            branch="loans", kind="loan"):
    db.execute(
        "INSERT INTO source_references("
        "source_reference_id,identity_id,provider_id,credential_id,account_id,"
        "branch_id,edition_id,right_id,reference_kind,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ref, identity, "paris", credential, account, branch,
         item, right, kind, 1, 1),
    )


def _link(db, ref, kind, source, identity, *, sha=SHA, proof="SECRET-LICENSE"):
    db.execute(
        "INSERT INTO origin_links("
        "origin_link_id,origin_kind,identity_id,artifact_id,"
        "source_reference_id,evidence_ref,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (ref, kind, identity, "sha256:" + sha, source, proof, 1, 1),
    )


def _review(storage, *, loan="paris-loan", item="item-1"):
    return review_paris_canonical_adept_links(
        storage, loan_id=loan, item_id=item,
    )


def test_missing_canonical_scope_is_unknown_not_inferred_from_ledger(tmp_path):
    storage = Storage(tmp_path / "empty.db")
    try:
        before = storage.schema_version()
        result = _review(storage)
        assert result["link_resolution"] == "unknown"
        assert result["review_reasons"] == ["canonical_source_reference_missing"]
        assert result["sources"] == []
        assert not result["return_authorized"]
        assert storage.schema_version() == before
    finally:
        storage.close()


def test_one_exact_source_with_provider_and_adept_origin_is_only_a_link_claim(tmp_path):
    storage = Storage(tmp_path / "single.db")
    try:
        _artifact(storage)
        with storage._lock, storage._connect() as db:
            _source(db, "right-alice")
            _link(db, "provider-alice", "provider", "right-alice", "alice",
                  proof="PROVIDER-SECRET")
            _link(db, "adobe-alice", "adept", "right-alice", "alice",
                  proof="ADOBE-LICENSE-SECRET")
        before = _review(storage)
        report = _review(storage)
        assert report == before
        assert report["link_resolution"] == "paired_links_unverified"
        assert report["source_count"] == 1
        assert report["multiple_sources"] is False
        assert report["review_reasons"] == []
        assert report["adobe_profile_resolution"] == "unknown"
        assert report["return_authorized"] is False
        artifact, = report["sources"][0]["artifacts"]
        assert artifact["paired_source_artifact_links"] is True
        assert artifact["provider_links"] == artifact["adept_links"] == 1
        assert artifact["adobe_profile_id"] is None
        assert artifact["adobe_loan_state"] == "unknown"
        assert "SECRET" not in repr(report)
    finally:
        storage.close()


def test_same_sha_two_borrowers_have_independent_rights_and_adept_origins(tmp_path):
    storage = Storage(tmp_path / "shared.db")
    try:
        _artifact(storage)
        with storage._lock, storage._connect() as db:
            _source(db, "right-alice", identity="alice", credential="paris-a")
            _source(db, "right-bob", identity="bob", credential="paris-b")
            _link(db, "provider-alice", "provider", "right-alice", "alice",
                  proof="provider-receipt-a")
            _link(db, "adept-alice", "adept", "right-alice", "alice",
                  proof="ADOBE-LOAN-A-SECRET")
            _link(db, "provider-bob", "provider", "right-bob", "bob",
                  proof="provider-receipt-b")
            _link(db, "adept-bob", "adept", "right-bob", "bob",
                  proof="ADOBE-LOAN-B-SECRET")
        result = _review(storage)
        assert result["link_resolution"] == "paired_links_unverified"
        assert result["source_count"] == 2
        assert result["multiple_sources"] is True
        assert result["review_reasons"] == ["multiple_paris_source_references"]
        assert {x["source_reference_id"] for x in result["sources"]} == {
            "right-alice", "right-bob",
        }
        assert all(x["artifacts"][0]["paired_source_artifact_links"]
                   for x in result["sources"])
        assert result["sources"][0]["artifacts"][0]["artifact_id"] == (
            result["sources"][1]["artifacts"][0]["artifact_id"]
        )
        assert result["adobe_profile_resolution"] == "unknown"
        assert "ADOBE-LOAN" not in repr(result)
        assert not result["return_authorized"]
    finally:
        storage.close()


def test_other_borrower_provider_link_cannot_complete_orphan_adobe_link(tmp_path):
    storage = Storage(tmp_path / "unpaired.db")
    try:
        _artifact(storage)
        with storage._lock, storage._connect() as db:
            _source(db, "right-alice")
            _source(db, "right-bob", identity="bob", credential="paris-b")
            _link(db, "adept-alice", "adept", "right-alice", "alice")
            _link(db, "provider-bob", "provider", "right-bob", "bob")
        result = _review(storage)
        assert result["link_resolution"] == "unpaired_or_missing_links"
        assert "canonical_origin_links_incomplete" in result["review_reasons"]
        assert result["sources"][0]["artifacts"][0]["provider_links"] == 0
        assert result["sources"][1]["artifacts"][0]["adept_links"] == 0
    finally:
        storage.close()


def test_cross_identity_adept_link_is_conflict_not_account_transfer(tmp_path):
    storage = Storage(tmp_path / "cross.db")
    try:
        _artifact(storage)
        with storage._lock, storage._connect() as db:
            _source(db, "right-alice")
            _link(db, "provider-alice", "provider", "right-alice", "alice")
            # The v14 ADEPT link does not have the same-identity SQL trigger.
            _link(db, "adept-bob", "adept", "right-alice", "bob")
        result = _review(storage)
        assert result["link_resolution"] == "conflicting_records"
        assert result["sources"][0]["cross_identity_link"] is True
        assert result["sources"][0]["artifacts"][0]["adept_links"] == 0
        assert "canonical_origin_links_conflict" in result["review_reasons"]
        assert not result["return_authorized"]
    finally:
        storage.close()


@pytest.mark.parametrize("mode", ["two_adobe_licenses", "two_artifacts"])
def test_multiple_adobe_claims_or_artifacts_are_not_silently_selected(tmp_path, mode):
    storage = Storage(tmp_path / "multiple.db")
    try:
        _artifact(storage)
        if mode == "two_artifacts":
            _artifact(storage, ALT_SHA)
        with storage._lock, storage._connect() as db:
            _source(db, "right-alice")
            _link(db, "provider-alice", "provider", "right-alice", "alice")
            _link(db, "adept-alice", "adept", "right-alice", "alice")
            if mode == "two_adobe_licenses":
                _link(db, "adept-alice-other", "adept", "right-alice", "alice",
                      proof="different-adobe-receipt")
            else:
                _link(db, "provider-alice-v2", "provider", "right-alice", "alice",
                      sha=ALT_SHA)
                _link(db, "adept-alice-v2", "adept", "right-alice", "alice",
                      sha=ALT_SHA)
        result = _review(storage)
        assert result["link_resolution"] == "conflicting_records"
        assert "canonical_origin_links_conflict" in result["review_reasons"]
        assert result["return_authorized"] is False
    finally:
        storage.close()


def test_other_item_or_right_and_purchase_kind_cannot_match(tmp_path):
    storage = Storage(tmp_path / "scope.db")
    try:
        _artifact(storage)
        with storage._lock, storage._connect() as db:
            _source(db, "other-loan", right="not-the-loan")
            _source(db, "other-item", item="different-item")
            _source(db, "other-kind", kind="purchase")
            for ref in ("other-loan", "other-item", "other-kind"):
                _link(db, "provider-" + ref, "provider", ref, "alice")
                _link(db, "adept-" + ref, "adept", ref, "alice")
        result = _review(storage)
        assert result["link_resolution"] == "unknown"
        assert result["sources"] == []
    finally:
        storage.close()


@pytest.mark.parametrize(("loan", "item"), [
    (" ", "item-1"), ("paris-loan", ""), ("\x00PRIVATE", "item-1"),
    ({"token": "PRIVATE"}, "item-1"), ("paris-loan", ["PRIVATE"]),
])
def test_malformed_exact_scope_is_refused_before_any_storage_access(
    tmp_path, loan, item,
):
    class NoDatabase:
        _lock = None

        def _connect(self):
            raise AssertionError("must not open DB for invalid scope")

    result = review_paris_canonical_adept_links(
        NoDatabase(), loan_id=loan, item_id=item,
    )
    assert result["link_resolution"] == "unknown"
    assert result["review_reasons"] == ["invalid_exact_scope"]
    assert result["sources"] == []
    assert "PRIVATE" not in repr(result)


@pytest.mark.parametrize(("invalid_kind", "invalid_receipt"), [
    ("adept", ""), ("adept", "  \t  "), ("provider", "  "),
])
def test_origin_link_without_actual_receipt_is_not_a_paired_adept_proof(
    tmp_path, invalid_kind, invalid_receipt,
):
    storage = Storage(tmp_path / "empty-origin-evidence.db")
    try:
        _artifact(storage)
        with storage._lock, storage._connect() as db:
            _source(db, "right-alice")
            _link(db, "provider-alice", "provider", "right-alice", "alice",
                  proof=invalid_receipt if invalid_kind == "provider" else "provider-receipt")
            _link(db, "adept-alice", "adept", "right-alice", "alice",
                  proof=invalid_receipt if invalid_kind == "adept" else "adept-receipt")
            before = db.execute(
                "SELECT count(*) FROM origin_links WHERE source_reference_id='right-alice'"
            ).fetchone()[0]
        report = _review(storage)
        assert report["link_resolution"] == "unpaired_or_missing_links"
        assert report["review_reasons"] == [
            "canonical_origin_links_incomplete", "canonical_origin_receipt_missing",
        ]
        assert report["source_count"] == 1
        artifact, = report["sources"][0]["artifacts"]
        assert artifact["provider_links"] == 1
        assert artifact["adept_links"] == 1
        assert artifact["links_without_receipt"] == 1
        assert artifact["paired_source_artifact_links"] is False
        assert report["adobe_profile_resolution"] == "unknown"
        assert report["return_authorized"] is False
        with storage._lock, storage._connect() as db:
            assert db.execute(
                "SELECT count(*) FROM origin_links WHERE source_reference_id='right-alice'"
            ).fetchone()[0] == before
    finally:
        storage.close()


def test_valid_second_borrower_does_not_fill_missing_adobe_receipt_for_first(
    tmp_path,
):
    storage = Storage(tmp_path / "partial-evidence.db")
    try:
        _artifact(storage)
        with storage._lock, storage._connect() as db:
            _source(db, "right-alice", identity="alice", credential="paris-a")
            _source(db, "right-bob", identity="bob", credential="paris-b")
            _link(db, "provider-alice", "provider", "right-alice", "alice")
            _link(db, "adept-alice", "adept", "right-alice", "alice", proof="")
            _link(db, "provider-bob", "provider", "right-bob", "bob",
                  proof="provider-bob")
            _link(db, "adept-bob", "adept", "right-bob", "bob",
                  proof="adept-bob")
        report = _review(storage)
        assert report["source_count"] == 2
        assert report["link_resolution"] == "unpaired_or_missing_links"
        by_id = {row["source_reference_id"]: row["artifacts"][0]
                 for row in report["sources"]}
        assert by_id["right-alice"]["paired_source_artifact_links"] is False
        assert by_id["right-bob"]["paired_source_artifact_links"] is True
        assert by_id["right-alice"]["artifact_id"] == by_id["right-bob"]["artifact_id"]
        assert "canonical_origin_receipt_missing" in report["review_reasons"]
        assert not report["return_authorized"]
    finally:
        storage.close()
