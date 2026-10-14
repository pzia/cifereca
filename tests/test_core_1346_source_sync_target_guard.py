"""P1 #1346: source-sync must never silently broaden a precise account target."""
from __future__ import annotations

import json

import pytest

from cifereca.brokers import build_public_brokers
from cifereca.plugin_api import SyncRequest
from tests.test_core039_contract_surface import Account, FakeService


def _setup():
    service = FakeService()
    service.accounts.append(Account(
        "reader-b", "kobo", "vault-b", "kobo-b", "Private Reader B",
    ))
    broker = build_public_brokers(service)["sync"]
    refs = {row.account_id: row for row in broker.sources()}
    return service, broker, refs


@pytest.mark.parametrize("request,expected", [
    (SyncRequest(identity_id="reader-b"), "identity_mismatch"),
    (SyncRequest(identity_id="alice", scopes=("materialize",)), "unsupported_scope"),
    (SyncRequest(identity_id="alice", scopes=("remote_inventory",)), "unsupported_scope"),
    (SyncRequest(identity_id="alice", options={"account_id": "a2"}), "target_mismatch"),
    (SyncRequest(identity_id="alice", options={"credential_id": "vault-b"}), "target_mismatch"),
    (SyncRequest(identity_id="alice", options={"provider_id": "kobo"}), "target_mismatch"),
    (SyncRequest(identity_id="alice", options={"unexpected_scope": "download"}), "target_mismatch"),
    (SyncRequest(identity_id="alice", deep=1), "unsupported_scope"),
])
def test_single_account_scope_mismatch_has_no_reads_writes_or_refresh(request, expected):
    service, broker, refs = _setup()
    # State/checkpoint must remain untouched even if source is disabled.
    before = dict(service.storage.values)
    response = broker.synchronize_source(refs["a1"].source_id, request)
    assert not response.ok and response.changed == 0
    assert response.facts == {"reason": expected}
    assert service.calls == []
    assert service.storage.values == before
    assert "vault-b" not in json.dumps(response.facts)


def test_exact_account_contract_can_refresh_only_requested_source():
    service, broker, refs = _setup()
    request = SyncRequest(
        identity_id="alice",
        options={
            "provider_id": "paris", "credential_id": "card-2",
            "account_id": "a2", "identity_id": "alice",
        },
    )
    completed = broker.synchronize_source(refs["a2"].source_id, request)
    assert completed.ok and completed.changed == 2
    assert service.calls == [("a2", False)]
    assert broker.sources("reader-b")[0].status == "idle"
    assert broker.sources("alice")[0].status == "idle"
    assert broker.sources("alice")[1].status == "ok"


@pytest.mark.parametrize("request,reason", [
    (SyncRequest(), "identity_required"),
    (SyncRequest(identity_id="", deep=True), "identity_required"),
    (SyncRequest(identity_id="alice", scopes=("materialize",)), "unsupported_scope"),
    (SyncRequest(identity_id="alice", options={"account_id": "a1"}), "unsupported_scope"),
    (SyncRequest(identity_id="alice", deep=1), "unsupported_scope"),
])
def test_global_batch_rejects_implicit_identity_or_materialization(request, reason):
    service, broker, refs = _setup()
    before = dict(service.storage.values)
    receipt = broker.synchronize(request)
    assert not receipt.ok and receipt.changed == 0
    assert receipt.facts == {"reason": reason}
    assert service.calls == []
    assert service.storage.values == before


def test_two_distinct_identities_cannot_refresh_each_other_by_reusing_source_id():
    service, broker, refs = _setup()
    assert refs["a1"].source_id != refs["kobo-b"].source_id
    blocked = broker.synchronize_source(
        refs["kobo-b"].source_id, SyncRequest(identity_id="alice"),
    )
    assert not blocked.ok and blocked.facts["reason"] == "identity_mismatch"
    assert service.calls == []
    allowed = broker.synchronize_source(
        refs["kobo-b"].source_id,
        SyncRequest(identity_id="reader-b", options={
            "identity_id": "reader-b", "provider_id": "kobo",
            "credential_id": "vault-b", "account_id": "kobo-b",
        }),
    )
    assert allowed.ok
    assert service.calls == [("kobo-b", False)]
