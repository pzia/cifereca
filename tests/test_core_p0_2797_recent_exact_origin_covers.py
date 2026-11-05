"""P0 #2797: recent covers must follow exact OriginLink borrower provenance.

Offline Core SQLite and synthetic files; same edition across two accounts must
not leak the other account's loan or purchase cover into a local placement.
"""
from cifereca.models import Loan
from tests.test_core_library_placement_identity_scope import _service
from tests.test_core_p0_2797_originlink_library_projection import (
    _local_artifact, _source_origin,
)

GOOD = "https://owner-a.invalid/cover.jpg"
FOREIGN = "https://owner-b.invalid/private-cover.jpg"


def _local_source_named_book(service, tmp_path):
    artifact_id, placement_id = _local_artifact(service, tmp_path, owner="alice")
    # Reproduce a historical descriptive catalogue source pair; it does not
    # by itself bind a credential or borrower.
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE placements SET provider_id=?,source_item_id=? "
            "WHERE placement_id=?",
            ("numilog", "edition-a", placement_id),
        )
        db.execute(
            "UPDATE catalog_entries SET provider_id=?,item_id=? "
            "WHERE placement_id=?",
            ("numilog", "edition-a", placement_id),
        )
    return artifact_id, placement_id


def _source_entitlement(service, *, credential, borrower, cover):
    service.storage.entitlement_put({
        "provider_id": "numilog", "identity_id": "alice",
        "credential_id": credential, "account_id": borrower,
        "item_id": "edition-a", "entitlement_type": "owned",
        "state": "active", "metadata": {"title": "Source book", "cover_url": cover},
    })


def _foreign_loan(service):
    service.last_loans = [Loan(
        id="foreign-loan", provider="Numilog", medium="digital",
        record_id="edition-a", title="Unrelated borrower",
        thumbnail_url=FOREIGN, source_provider="numilog",
        source_credential="card-b", source_account_id="buyer-b",
        identity_id="alice",
    )]


def test_recent_uses_only_exact_borrower_origin_for_remote_covers(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, placement_id = _local_source_named_book(service, tmp_path)
        _source_origin(
            service, identity="alice", provider="numilog",
            credential="card-a", account="buyer-a", edition="edition-a",
            artifact_id=artifact_id, token="a",
        )
        _source_entitlement(
            service, credential="card-a", borrower="buyer-a", cover=GOOD,
        )
        _source_entitlement(
            service, credential="card-b", borrower="buyer-b", cover=FOREIGN,
        )
        _foreign_loan(service)  # Foreign loan has a higher cover score (90).
        rows = service.recent_acquisitions(identity_id="alice")
        assert len(rows) == 1 and rows[0]["placement_id"] == placement_id
        assert rows[0]["thumbnail_url"] == GOOD
        cover = rows[0]["metadata"]["cover_resolution"]
        assert cover["source"] == "entitlement"
        assert cover["candidate_count"] == 1
        assert FOREIGN not in repr(rows)
    finally:
        service.close()


def test_descriptive_provider_item_without_origin_cannot_attach_private_cover(tmp_path):
    service = _service(tmp_path)
    try:
        _artifact, placement_id = _local_source_named_book(service, tmp_path)
        _source_entitlement(
            service, credential="card-b", borrower="buyer-b", cover=FOREIGN,
        )
        _foreign_loan(service)
        rows = service.recent_acquisitions(identity_id="alice")
        assert len(rows) == 1 and rows[0]["placement_id"] == placement_id
        assert rows[0]["thumbnail_url"] is None
        assert rows[0]["metadata"]["cover_resolution"]["status"] == "missing"
        assert FOREIGN not in repr(rows)
    finally:
        service.close()


def test_second_attested_account_on_same_artifact_may_supply_its_own_cover(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, placement_id = _local_source_named_book(service, tmp_path)
        _source_origin(
            service, identity="alice", provider="numilog",
            credential="card-b", account="buyer-b", edition="edition-a",
            artifact_id=artifact_id, token="b",
        )
        _source_entitlement(
            service, credential="card-b", borrower="buyer-b", cover=FOREIGN,
        )
        _foreign_loan(service)
        rows = service.recent_acquisitions(identity_id="alice")
        assert len(rows) == 1 and rows[0]["placement_id"] == placement_id
        assert rows[0]["thumbnail_url"] == FOREIGN
        # A purchase OriginLink is not a loan even for the same borrower.
        assert rows[0]["metadata"]["cover_resolution"]["source"] == "entitlement"
    finally:
        service.close()


def test_attested_loan_origin_prefers_loan_cover_not_purchase_for_same_borrower(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, placement_id = _local_source_named_book(service, tmp_path)
        _source_origin(
            service, identity="alice", provider="numilog",
            credential="card-b", account="buyer-b", edition="edition-a",
            artifact_id=artifact_id, token="b", kind="loan",
        )
        _source_entitlement(
            service, credential="card-b", borrower="buyer-b", cover=GOOD,
        )
        _foreign_loan(service)
        rows = service.recent_acquisitions(identity_id="alice")
        assert len(rows) == 1 and rows[0]["placement_id"] == placement_id
        assert rows[0]["thumbnail_url"] == FOREIGN
        assert rows[0]["metadata"]["cover_resolution"]["source"] == "loan"
        assert GOOD not in repr(rows)
    finally:
        service.close()


def test_purchase_origin_never_promotes_same_borrower_loan_cover_without_loan_link(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, placement_id = _local_source_named_book(service, tmp_path)
        _source_origin(
            service, identity="alice", provider="numilog",
            credential="card-b", account="buyer-b", edition="edition-a",
            artifact_id=artifact_id, token="b",
        )
        _foreign_loan(service)
        rows = service.recent_acquisitions(identity_id="alice")
        assert len(rows) == 1 and rows[0]["placement_id"] == placement_id
        assert rows[0]["thumbnail_url"] is None
        assert rows[0]["metadata"]["cover_resolution"]["status"] == "missing"
        assert FOREIGN not in repr(rows)
    finally:
        service.close()
