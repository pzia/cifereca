"""P0 #2797/#3196: a legacy catalogue label is not remote account proof.

No provider network, no rights mutation, no SHA/title-based ownership.
"""
from __future__ import annotations

import pytest

from tests.test_core_library_placement_identity_scope import _service
from tests.test_core_p0_2797_originlink_library_projection import (
    _entitlement, _local_artifact, _source_origin,
)


@pytest.mark.parametrize("credential,account", [
    ("card-a", "buyer-a"),
    ("foreign-card", "foreign-buyer"),
])
def test_unverified_provider_catalogue_alias_never_folds_remote_entitlement(
    tmp_path, credential, account,
):
    service = _service(tmp_path)
    try:
        artifact, placement_id = _local_artifact(service, tmp_path, owner="alice")
        existing = service.storage.placement_get(placement_id)
        path = existing["path"]
        # A historical catalogue row has a matching source label and file
        # identifier, but no exact OriginLink/SourceReference for ANY account.
        service.storage.catalog_entry_put({
            "artifact_id": artifact,
            "placement_id": placement_id,
            "title": "Edition from old cache",
            "identity_id": "alice",
            "provider_id": "numilog",
            "item_id": "edition-a",
            "local_state": "local",
            "media_format": "epub",
            "metadata": {"path": path},
        })
        _entitlement(
            service, identity="alice", credential=credential,
            account=account, edition="edition-a",
        )
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
        before = service.storage.placements()
        rows = service.library_entries(identity_id="alice", scan=False)
        local = [row for row in rows if row["local"]]
        remote = [row for row in rows if not row["local"]]
        assert len(local) == len(remote) == 1
        assert local[0]["artifact_id"] == artifact
        assert local[0]["possessions"] == []
        assert remote[0]["artifact_id"] is None
        assert [p["account_id"] for p in remote[0]["possessions"]] == [account]
        assert remote[0]["key"] != local[0]["key"]
        assert service.storage.placements() == before
    finally:
        service.close()


def test_exact_originlink_still_folds_when_legacy_label_exists(tmp_path):
    service = _service(tmp_path)
    try:
        artifact, _ = _local_artifact(service, tmp_path, owner="alice")
        _source_origin(
            service, identity="alice", provider="numilog",
            credential="card-a", account="buyer-a",
            edition="edition-a", artifact_id=artifact, token="verified-a",
        )
        _entitlement(
            service, identity="alice", credential="card-a",
            account="buyer-a", edition="edition-a",
        )
        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 1
        assert rows[0]["local"] is True
        assert rows[0]["artifact_id"] == artifact
        assert len(rows[0]["possessions"]) == 1
        assert rows[0]["possessions"][0]["account_id"] == "buyer-a"
    finally:
        service.close()
