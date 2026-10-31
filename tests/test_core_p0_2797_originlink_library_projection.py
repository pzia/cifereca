"""#2797: canonical OriginLink provenance unifies local/source cards safely.

The fixture uses only SQLite and synthetic files. No provider HTTP request,
content-title matching, credential fallback or filesystem mutation in Sync.
"""
import hashlib

from tests.test_core_library_placement_identity_scope import _placement, _service


def _source_origin(service, *, identity, provider, credential, account,
                   edition, artifact_id, token):
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,state,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"reference-{token}", identity, provider, credential, account,
             "purchases", edition, f"right-{token}", "purchase", "active", 1, 1),
        )
        db.execute(
            "INSERT INTO origin_links("
            "origin_link_id,origin_kind,identity_id,artifact_id,"
            "source_reference_id,evidence_ref,created_at,updated_at)"
            "VALUES(?,?,?,?,?,?,?,?)",
            (f"origin-{token}", "provider", identity, artifact_id,
             f"reference-{token}", "confirmed-receipt", 1, 1),
        )


def _local_artifact(service, tmp_path, *, owner):
    content = b"same epub contents"
    digest = hashlib.sha256(content).hexdigest()
    artifact_id = "sha256:" + digest
    service.storage.artifact_put({
        "artifact_id": artifact_id, "sha256": digest,
        "size": len(content), "media_format": "epub",
    })
    pid = _placement(
        service, tmp_path, owner=owner,
        filename=f"{owner}-owned.epub", artifact_id=artifact_id,
        item_id=f"local-{owner}",
    )
    return artifact_id, pid


def _entitlement(service, *, identity, credential, account, edition):
    service.storage.entitlement_put({
        "provider_id": "numilog", "identity_id": identity,
        "credential_id": credential, "account_id": account,
        "item_id": edition, "entitlement_type": "owned", "state": "active",
        "metadata": {"title": "Source-owned book"},
    })


def test_exact_originlink_folds_remote_numilog_card_into_local_artifact(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, pid = _local_artifact(service, tmp_path, owner="alice")
        _source_origin(
            service, identity="alice", provider="numilog",
            credential="card-a", account="buyer-a",
            edition="edition-a", artifact_id=artifact_id, token="a",
        )
        _entitlement(
            service, identity="alice", credential="card-a",
            account="buyer-a", edition="edition-a",
        )

        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 1
        row = rows[0]
        assert row["local"] is True
        assert row["artifact_id"] == artifact_id
        assert row["placement_id"] == pid
        assert row["providers"] == ["local", "numilog"]
        assert {"provider_id": "numilog", "item_id": "edition-a",
                "identity_id": "alice"} in row["source_refs"]
        assert len(row["possessions"]) == 1
        assert row["possessions"][0]["account_id"] == "buyer-a"
    finally:
        service.close()


def test_same_edition_other_account_does_not_inherit_verified_origin(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, _pid = _local_artifact(service, tmp_path, owner="alice")
        _source_origin(
            service, identity="alice", provider="numilog",
            credential="card-a", account="buyer-a",
            edition="edition-a", artifact_id=artifact_id, token="a",
        )
        _entitlement(
            service, identity="alice", credential="card-a",
            account="buyer-a", edition="edition-a",
        )
        _entitlement(
            service, identity="alice", credential="card-b",
            account="buyer-b", edition="edition-a",
        )
        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 2
        local = next(r for r in rows if r["local"])
        remote = next(r for r in rows if not r["local"])
        assert [x["account_id"] for x in local["possessions"]] == ["buyer-a"]
        assert [x["account_id"] for x in remote["possessions"]] == ["buyer-b"]
        assert remote["artifact_id"] is None
    finally:
        service.close()


def test_shared_sha_other_identity_never_inherits_source_provenance(tmp_path):
    service = _service(tmp_path)
    try:
        artifact_id, _pid = _local_artifact(service, tmp_path, owner="alice")
        _local_artifact(service, tmp_path, owner="bob")
        _source_origin(
            service, identity="alice", provider="numilog",
            credential="card-a", account="buyer-a",
            edition="edition-a", artifact_id=artifact_id, token="a",
        )
        alice = service.library_entries(identity_id="alice", scan=False)
        bob = service.library_entries(identity_id="bob", scan=False)
        assert len(alice) == len(bob) == 1
        assert "numilog" in alice[0]["providers"]
        assert bob[0]["providers"] == ["local"]
        assert all(ref["provider_id"] != "numilog" for ref in bob[0]["source_refs"])
    finally:
        service.close()
