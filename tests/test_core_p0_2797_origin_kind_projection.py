"""P0 #2797: purchase vs loan is part of exact OriginLink display proof.

Read-only Library projection, disposable SQLite v14 and synthetic local bytes.
No entitlement can attach to a different reference_kind with matching book ID.
"""
from cifereca.models import Loan
from cifereca.core.library_projection import _verified_provider_origin_coordinates
from tests.test_core_library_placement_identity_scope import _service
from tests.test_core_p0_2797_originlink_library_projection import (
    _entitlement, _local_artifact, _source_origin,
)


def _claim(service, artifact_id, *, token, kind="purchase"):
    _source_origin(
        service, identity="alice", provider="numilog",
        credential="card-a", account="buyer-a",
        edition="edition-a", artifact_id=artifact_id,
        token=token, kind=kind,
    )


def _loan_entitlement(service):
    service.storage.entitlement_put({
        "provider_id": "numilog", "identity_id": "alice",
        "credential_id": "card-a", "account_id": "buyer-a",
        "item_id": "edition-a", "entitlement_type": "loan",
        "state": "active", "loan_id": "loan-one",
        "metadata": {"title": "A loan, not a purchase"},
    })


def test_purchase_origin_does_not_grant_same_edition_loan_possession(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, pid = _local_artifact(service, tmp_path, owner="alice")
        _claim(service, artifact_id, token="purchase")
        _loan_entitlement(service)
        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 2
        local = next(r for r in rows if r["local"])
        remote = next(r for r in rows if not r["local"])
        assert local["placement_id"] == pid
        assert local["possessions"] == []
        assert remote["entitlement_type"] == "loan"
        assert remote["artifact_id"] is None
    finally:
        service.close()


def test_loan_origin_does_not_grant_same_edition_owned_possession(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, pid = _local_artifact(service, tmp_path, owner="alice")
        _claim(service, artifact_id, token="loan", kind="loan")
        _entitlement(
            service, identity="alice", credential="card-a",
            account="buyer-a", edition="edition-a",
        )
        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 2
        local = next(r for r in rows if r["local"])
        remote = next(r for r in rows if not r["local"])
        assert local["placement_id"] == pid
        assert local["possessions"] == []
        assert remote["entitlement_type"] == "owned"
        assert remote["artifact_id"] is None
    finally:
        service.close()


def test_each_genuine_purchase_and_loan_origin_folds_only_its_own_possession(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, pid = _local_artifact(service, tmp_path, owner="alice")
        _claim(service, artifact_id, token="purchase")
        _claim(service, artifact_id, token="loan", kind="loan")
        _entitlement(
            service, identity="alice", credential="card-a",
            account="buyer-a", edition="edition-a",
        )
        _loan_entitlement(service)
        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 1
        assert rows[0]["placement_id"] == pid
        assert {p["kind"] for p in rows[0]["possessions"]} == {"owned", "loan"}
        assert len(rows[0]["possessions"]) == 2
    finally:
        service.close()


def test_existing_six_coordinate_reader_is_preserved_by_opt_in_kind(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, pid = _local_artifact(service, tmp_path, owner="alice")
        _claim(service, artifact_id, token="purchase")
        old = _verified_provider_origin_coordinates(
            service.storage, identity_id="alice",
        )
        assert old == ((
            artifact_id, "alice", "numilog", "card-a",
            "buyer-a", "edition-a",
        ),)
        typed = _verified_provider_origin_coordinates(
            service.storage, identity_id="alice", with_kind=True,
        )
        assert typed == (old[0] + ("purchase",),)
        assert service.storage.placement_get(pid)["state"] == "present"
    finally:
        service.close()


def test_owned_possession_cannot_inherit_loan_cover_from_same_account(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, pid = _local_artifact(service, tmp_path, owner="alice")
        _claim(service, artifact_id, token="purchase")
        _entitlement(
            service, identity="alice", credential="card-a",
            account="buyer-a", edition="edition-a",
        )
        private_loan_cover = "https://private.example/LOAN_ONLY_COVER"
        service.last_loans = [Loan(
            id="loan-a", provider="Numilog", medium="digital",
            record_id="edition-a", title="A different loan title",
            thumbnail_url=private_loan_cover,
            source_provider="numilog", source_credential="card-a",
            source_account_id="buyer-a", identity_id="alice",
        )]
        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 1 and rows[0]["placement_id"] == pid
        assert rows[0]["possessions"][0]["kind"] == "owned"
        assert rows[0]["thumbnail_url"] is None
        assert private_loan_cover not in repr(rows)
        assert "A different loan title" not in repr(rows)
    finally:
        service.close()
