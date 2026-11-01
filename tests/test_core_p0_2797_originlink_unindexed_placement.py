"""P0 #2797: read-only fallback for exact OriginLink + physical Placement.

A canonical acquisition may have published a Placement while its local
catalogue index is absent. Never invent provenance from a matching SHA.
"""
from pathlib import Path

from tests.test_core_library_placement_identity_scope import _placement, _service
from tests.test_core_p0_2797_originlink_library_projection import (
    _entitlement, _local_artifact, _source_origin,
)


def _drop_catalog(service, *placement_ids):
    with service.storage._lock, service.storage._connect() as db:
        for placement_id in placement_ids:
            db.execute(
                "DELETE FROM catalog_entries WHERE placement_id=?",
                (placement_id,),
            )


def _origin_and_right(service, artifact_id):
    _source_origin(
        service, identity="alice", provider="numilog",
        credential="card-a", account="buyer-a",
        edition="edition-a", artifact_id=artifact_id, token="a",
    )
    _entitlement(
        service, identity="alice", credential="card-a",
        account="buyer-a", edition="edition-a",
    )


def test_unindexed_present_placement_folds_exact_owned_origin(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, pid = _local_artifact(service, tmp_path, owner="alice")
        _origin_and_right(service, artifact_id)
        _drop_catalog(service, pid)

        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 1
        row = rows[0]
        assert row["local"] is True
        assert row["source"] == "placement"
        assert row["artifact_id"] == artifact_id
        assert row["placement_id"] == pid
        assert row["providers"] == ["local", "numilog"]
        assert row["source_refs"] == [
            {"provider_id": "numilog", "item_id": "edition-a",
             "identity_id": "alice"},
        ]
        assert [p["account_id"] for p in row["possessions"]] == ["buyer-a"]
        assert row["title"] == "Source-owned book"
        assert service.storage.catalog_entries() == []
    finally:
        service.close()


def test_unindexed_origin_never_merges_other_borrower_account(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, pid = _local_artifact(service, tmp_path, owner="alice")
        _origin_and_right(service, artifact_id)
        _entitlement(
            service, identity="alice", credential="card-b",
            account="buyer-b", edition="edition-a",
        )
        _drop_catalog(service, pid)

        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 2
        local = next(row for row in rows if row["local"])
        remote = next(row for row in rows if not row["local"])
        assert [p["account_id"] for p in local["possessions"]] == ["buyer-a"]
        assert [p["account_id"] for p in remote["possessions"]] == ["buyer-b"]
        assert remote["artifact_id"] is None
    finally:
        service.close()


def test_unindexed_origin_is_never_projected_into_other_identity(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, pid = _local_artifact(service, tmp_path, owner="alice")
        _local_artifact(service, tmp_path, owner="bob")
        _origin_and_right(service, artifact_id)
        _drop_catalog(service, pid)

        alice = service.library_entries(identity_id="alice", scan=False)
        bob = service.library_entries(identity_id="bob", scan=False)
        assert len(alice) == len(bob) == 1
        assert "numilog" in alice[0]["providers"]
        assert bob[0]["providers"] == ["local"]
        assert bob[0]["possessions"] == []
        assert all(
            row.get("identity_id") == "bob" for row in bob[0]["placements"]
        )
    finally:
        service.close()


def test_unindexed_missing_file_never_claims_local_availability(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, pid = _local_artifact(service, tmp_path, owner="alice")
        _origin_and_right(service, artifact_id)
        _drop_catalog(service, pid)
        Path(service.storage.placement_get(pid)["path"]).unlink()

        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 1
        assert rows[0]["local"] is False
        assert rows[0]["artifact_id"] is None
        assert rows[0]["state"] == "remote_owned"
    finally:
        service.close()


def test_unindexed_two_placements_keep_both_in_one_local_card(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, pid = _local_artifact(service, tmp_path, owner="alice")
        pid2 = _placement(
            service, tmp_path, owner="alice", filename="copy.epub",
            artifact_id=artifact_id, item_id="copy",
        )
        _origin_and_right(service, artifact_id)
        _drop_catalog(service, pid, pid2)

        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 1
        assert rows[0]["placement_count"] == 2
        assert {p["placement_id"] for p in rows[0]["placements"]} == {pid, pid2}
        assert rows[0]["providers"] == ["local", "numilog"]
    finally:
        service.close()
