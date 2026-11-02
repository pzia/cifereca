"""#2797: remote-only Library cards must not mix distinct borrower rights.

No HTTP, acquisition, file mutation or new provenance. Exact canonical local
OriginLinks keep their existing fusion authority.
"""
from __future__ import annotations

import json

from tests.test_core_library_placement_identity_scope import _service
from tests.test_core_p0_2797_originlink_library_projection import (
    _entitlement, _local_artifact, _source_origin,
)


def _right(service, *, ident="alice", credential="card-a", account="buyer-a",
           entitlement_id="right-a", title="A purchase", item="edition-a",
           provider="numilog"):
    service.storage.entitlement_put({
        "entitlement_id": entitlement_id,
        "identity_id": ident,
        "provider_id": provider,
        "credential_id": credential,
        "account_id": account,
        "item_id": item,
        "entitlement_type": "owned",
        "state": "active",
        "metadata": {"title": title},
    })


def test_two_accounts_same_provider_item_do_not_merge_remote_possessions(tmp_path):
    service = _service(tmp_path)
    try:
        _right(service, entitlement_id="right-a", account="buyer-a",
               credential="card-a", title="Private A")
        _right(service, entitlement_id="right-b", account="buyer-b",
               credential="card-b", title="Private B")
        before = service.storage.entitlements()
        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 2
        assert {r["title"] for r in rows} == {"Private A", "Private B"}
        assert len({r["key"] for r in rows}) == 2
        assert all(r["state"] == "remote_owned" and not r["local"] for r in rows)
        assert all(len(r["possessions"]) == 1 for r in rows)
        by_title = {r["title"]: r for r in rows}
        assert by_title["Private A"]["possessions"][0]["account_id"] == "buyer-a"
        assert by_title["Private B"]["possessions"][0]["account_id"] == "buyer-b"
        assert by_title["Private A"]["entitlement_id"] == "right-a"
        assert by_title["Private B"]["entitlement_id"] == "right-b"
        assert service.storage.entitlements() == before
        assert {r["key"] for r in service.library_entries(
            identity_id="alice", scan=False,
        )} == {r["key"] for r in rows}
        # Display keys contain no raw account, credential, title or edition.
        for row in rows:
            key = row["key"]
            assert key.startswith("source:scoped:")
            assert len(key) == len("source:scoped:") + 64
            assert all(private not in key for private in (
                "buyer-a", "buyer-b", "card-a", "card-b", "edition-a",
                "Private A", "Private B",
            ))
    finally:
        service.close()


def test_two_rights_same_account_still_keep_distinct_remote_rows(tmp_path):
    service = _service(tmp_path)
    try:
        _right(service, entitlement_id="right-first", title="Edition one")
        _right(service, entitlement_id="right-second", title="Edition two")
        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 2
        assert {r["entitlement_id"] for r in rows} == {
            "right-first", "right-second",
        }
        assert all(len(r["possessions"]) == 1 for r in rows)
    finally:
        service.close()


def test_other_identity_does_not_inherit_unlinked_right(tmp_path):
    service = _service(tmp_path)
    try:
        _right(service, ident="alice", entitlement_id="right-alice")
        _right(service, ident="bob", entitlement_id="right-bob")
        alice = service.library_entries(identity_id="alice", scan=False)
        bob = service.library_entries(identity_id="bob", scan=False)
        assert len(alice) == len(bob) == 1
        assert alice[0]["entitlement_id"] == "right-alice"
        assert bob[0]["entitlement_id"] == "right-bob"
        assert alice[0]["key"] != bob[0]["key"]
    finally:
        service.close()


def test_originlink_positive_still_folds_scoped_right_to_local_artifact(tmp_path):
    service = _service(tmp_path)
    try:
        artifact, _pid = _local_artifact(service, tmp_path, owner="alice")
        _source_origin(
            service, identity="alice", provider="numilog",
            credential="card-a", account="buyer-a",
            edition="edition-a", artifact_id=artifact, token="canonical-a",
        )
        _entitlement(
            service, identity="alice", credential="card-a",
            account="buyer-a", edition="edition-a",
        )
        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 1
        assert rows[0]["local"] and rows[0]["artifact_id"] == artifact
        assert rows[0]["key"].startswith("artifact:")
        assert len(rows[0]["possessions"]) == 1
    finally:
        service.close()
