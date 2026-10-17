"""Account-scoped, bounded SQLite evidence without acquisition authority."""
from dataclasses import replace
import hashlib
import sqlite3

import pytest

from cifereca.core import numilog_local_preview
from cifereca.storage import Storage
from tests.test_core_p0_1905_numilog_offline_preview import (
    _broker, _ledger, _owned, _preview,
)
from tests.test_core_p0_1862_numilog_store_mirror import _connect
from tests.test_sync_state_contract import _service


SCOPE = dict(provider_id="store", credential_id="credential", account_id="account", identity_id="alice")


def seed(storage, tmp_path, index=0, **scope):
    scope = {**SCOPE, **scope}
    item = f"item-{index}"
    eid = "right-" + scope["credential_id"] + item
    digest = hashlib.sha256(item.encode()).hexdigest()
    artifact = "sha256:" + digest
    storage.entitlement_put({
        **scope, "entitlement_id": eid, "item_id": item,
        "entitlement_type": "owned", "state": "active", "metadata": {"source": "private-library"},
    })
    storage.acquisition_put({
        **scope, "acquisition_id": "acquired-" + scope["credential_id"] + item,
        "entitlement_id": eid, "item_id": item, "artifact_id": artifact, "status": "complete",
    })
    storage.artifact_put({"artifact_id": artifact, "sha256": digest, "size": 10, "media_format": "epub"})
    storage.placement_put({
        "artifact_id": artifact, "path": str(tmp_path / scope["identity_id"] / f"{index}.epub"),
        "identity_id": scope["identity_id"], "provider_id": scope["provider_id"],
        "source_item_id": item, "state": "present",
    })


def test_shared_artifact_only_loads_exact_identity_and_purchase_placement(tmp_path):
    storage = Storage(tmp_path / "evidence.sqlite")
    try:
        seed(storage, tmp_path)
        seed(storage, tmp_path, credential_id="other", identity_id="bob")
        evidence = storage.source_materialization_evidence(**SCOPE, max_rows=1)
        assert {name: len(rows) for name, rows in evidence.items()} == {
            "entitlements": 1, "acquisitions": 1, "placements": 1, "artifacts": 1,
        }
        assert evidence["placements"][0]["identity_id"] == "alice"
        assert evidence["artifacts"][0]["kind"] == "file"
        assert "metadata_json" not in evidence["entitlements"][0]
    finally:
        storage.close()


def test_conflicting_ledger_owner_is_retained_without_foreign_placements(tmp_path):
    storage = Storage(tmp_path / "evidence.sqlite")
    try:
        seed(storage, tmp_path)
        original = storage.acquisitions()[0]
        storage.acquisition_put({
            **original, "acquisition_id": "foreign-owner", "identity_id": "bob", "account_id": "other-account",
        })
        evidence = storage.source_materialization_evidence(**SCOPE)
        assert len(evidence["acquisitions"]) == 2
        assert {row["identity_id"] for row in evidence["acquisitions"]} == {"alice", "bob"}
        # A duplicate acquisition must not multiply the placement/artifact.
        assert len(evidence["placements"]) == len(evidence["artifacts"]) == 1
    finally:
        storage.close()


@pytest.mark.parametrize("table", ["entitlements", "acquisitions", "placements"])
def test_budget_fails_closed_and_releases_read_transaction(tmp_path, table):
    storage = Storage(tmp_path / "evidence.sqlite")
    try:
        seed(storage, tmp_path)
        if table == "entitlements":
            seed(storage, tmp_path, 1)
        elif table == "acquisitions":
            storage.acquisition_put({**storage.acquisitions()[0], "acquisition_id": "duplicate"})
        else:
            storage.placement_put({
                **storage.placements()[0], "placement_id": None, "path": str(tmp_path / "duplicate.epub"),
            })
        db = storage._connect()
        before = db.total_changes
        with pytest.raises(ValueError, match="^evidence_budget_exhausted$"):
            storage.source_materialization_evidence(**SCOPE, max_rows=1)
        assert not db.in_transaction
        assert db.total_changes == before
        assert storage.source_materialization_evidence(**SCOPE, max_rows=2)[table]
    finally:
        storage.close()


@pytest.mark.parametrize("number", [0, 1, 200])
def test_query_count_is_constant_and_snapshot_is_read_only(tmp_path, number):
    storage = Storage(tmp_path / "evidence.sqlite")
    try:
        for index in range(number):
            seed(storage, tmp_path, index)
        statements = []
        db = storage._connect()
        before = db.total_changes
        db.set_trace_callback(statements.append)
        evidence = storage.source_materialization_evidence(**SCOPE)
        db.set_trace_callback(None)
        assert all(len(rows) == number for rows in evidence.values())
        assert len(statements) == 6  # savepoint, four bounded reads, release
        assert db.total_changes == before
        assert not db.in_transaction
    finally:
        storage.close()


def test_other_process_commit_cannot_mix_rights_and_acquisition_snapshots(tmp_path):
    storage = Storage(tmp_path / "evidence.sqlite")
    try:
        seed(storage, tmp_path)
        updated = []

        def external_writer(sql):
            if "SELECT acquisition_id," in sql and not updated:
                with sqlite3.connect(storage.path, timeout=1) as writer:
                    writer.execute("UPDATE acquisition_ledger SET status='blocked'")
                    writer.execute("UPDATE entitlements SET state='revoked'")
                updated.append(True)

        db = storage._connect()
        db.set_trace_callback(external_writer)
        evidence = storage.source_materialization_evidence(**SCOPE)
        db.set_trace_callback(None)
        assert updated == [True]
        assert evidence["entitlements"][0]["state"] == "active"
        assert evidence["acquisitions"][0]["status"] == "complete"
        assert all(not rows for rows in storage.source_materialization_evidence(**SCOPE).values())
        assert storage.acquisitions()[0]["status"] == "blocked"
    finally:
        storage.close()


@pytest.mark.parametrize("options", [
    {"identity_id": ""}, {"account_id": None}, {"credential_id": "\0secret"},
    {"provider_id": "a\nsecret"}, {"max_rows": True}, {"max_rows": 0}, {"max_rows": 100_001},
])
def test_invalid_scope_or_budget_is_refused_before_sql(tmp_path, options):
    storage = Storage(tmp_path / "evidence.sqlite")
    try:
        statements = []
        storage._connect().set_trace_callback(statements.append)
        with pytest.raises(ValueError, match="^invalid_evidence_scope$"):
            storage.source_materialization_evidence(**{**SCOPE, **options})
        assert statements == []
    finally:
        storage.close()


@pytest.mark.parametrize("guard_global_lists", [False, True])
def test_preview_budget_ignores_other_accounts_and_unrelated_local_files(
    tmp_path, monkeypatch, guard_global_lists,
):
    service = _service(tmp_path)
    try:
        account, _, calls = _connect(service, number=0)
        foreign, _, foreign_calls = _connect(service, identity="alex", credential="other", number=0)
        item, eid = _owned(service, account)
        _ledger(service, account, item, eid)
        for index in range(5):
            _owned(service, foreign, str(index))
            seed(service.storage, tmp_path, index)
        monkeypatch.setattr(numilog_local_preview, "_MAX_ROWS", 1)
        if guard_global_lists:
            for name in ("entitlements", "acquisitions", "placements", "artifacts"):
                monkeypatch.setattr(service.storage, name, lambda **kw: pytest.fail("global enumeration"))
        broker, sid = _broker(service, account)
        preview = _preview(broker, sid, verify=True)
        assert preview.ok
        assert preview.facts["counts"]["owned_purchases"] == 1
        assert preview.facts["counts"]["already_local_verified"] == 1
        assert preview.facts["acquisition_authorized"] is False
        assert calls == foreign_calls == []
    finally:
        service.close()


def test_preview_rejects_oversized_own_evidence_without_partial_counts(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        account, _, calls = _connect(service, number=0)
        _owned(service, account, "1")
        _owned(service, account, "2")
        monkeypatch.setattr(numilog_local_preview, "_MAX_ROWS", 1)
        broker, sid = _broker(service, account)
        preview = _preview(broker, sid)
        assert not preview.ok
        assert preview.facts["status"] == "evidence_budget_exhausted"
        assert "counts" not in preview.facts
        assert preview.facts["acquisition_authorized"] is False
        assert calls == []
    finally:
        service.close()


def test_foreign_only_ledger_does_not_turn_into_a_missing_download_candidate(tmp_path):
    service = _service(tmp_path)
    try:
        account, _, calls = _connect(service, number=0)
        item, eid = _owned(service, account)
        _ledger(service, replace(account, identity_id="alex", unique_id="other-account"), item, eid)
        broker, sid = _broker(service, account)
        preview = _preview(broker, sid)
        assert preview.ok
        assert preview.facts["counts"]["needs_reconciliation"] == 1
        assert preview.facts["counts"]["potentially_missing"] == 0
        assert preview.facts["counts"]["already_local_linked"] == 0
        assert calls == []
    finally:
        service.close()
