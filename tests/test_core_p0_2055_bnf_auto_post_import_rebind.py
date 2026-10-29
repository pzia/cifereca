"""P0 #2055: in-flight BnF auto consent/session revocation is never success.

Synthetic BnF HTTPX MockTransport / Core, no live account or ADEPT import.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import bnf_auto_materialization as auto
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


@pytest.mark.parametrize("change", [
    "consent_revoked", "consent_reissued", "provider_reconnected", "core_rebound",
])
def test_bnf_auto_import_cannot_publish_stale_positive_claim(
    connected_core, monkeypatch, change,
):
    service, session, account, loan, _state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    assert broker.set_bnf_auto_download(sid, True)
    calls = []

    def import_during_rebind(candidate, **kwargs):
        calls.append((candidate, kwargs))
        if change in {"consent_revoked", "consent_reissued"}:
            broker.set_bnf_auto_download(sid, False)
            if change == "consent_reissued":
                # A→B→A consent changes do not leave the old run authorized.
                broker.set_bnf_auto_download(sid, True)
        elif change == "provider_reconnected":
            service._sessions[service._session_key("bnf-pret", account.credential_id)] = NS(connected=False, close=lambda: None)
        else:
            service._source_context_epoch += 1
        return {"status": "complete", "acquisition_id": "synthetic-claim"}

    monkeypatch.setattr(service, "import_acsm", import_during_rebind)
    updated = []
    monkeypatch.setattr(
        service.storage, "acquisition_update_state",
        lambda *a, **kw: updated.append((a, kw))
        or pytest.fail("stale BnF account published auto-ref"),
    )
    try:
        result = auto.materialize(service, account, loan)
        assert result == {
            "status": "attention_required", "code": "bnf_loan_scope_unproven",
        }
        assert len(downloaded) == 1
        assert len(calls) == 1
        assert updated == []
        assert service.storage.acquisitions(provider_id="manual") == []
    finally:
        service.close()


def test_bnf_verified_existing_receipt_not_marked_unchanged_after_consent_revoke(
    connected_core, monkeypatch, tmp_path,
):
    service, session, account, loan, _state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    assert broker.set_bnf_auto_download(sid, True)
    ref = hashlib.sha256("\0".join((
        account.identity_id, account.credential_id,
        account.unique_id, loan.id, loan.record_id,
    )).encode()).hexdigest()
    # Only a synthetic scoped receipt: the file is never opened because the
    # verifier is deliberately interrupted mid-inspection.
    service.storage.acquisition_put({
        "acquisition_id": "manual-acsm:synthetic-previous",
        "identity_id": account.identity_id, "provider_id": "manual",
        "credential_id": "manual", "item_id": "synthetic-item",
        "status": "complete", "artifact_id": "sha256:" + "a" * 64,
        "metadata": {"bnf_auto_ref": ref, "path": str(tmp_path / "synthetic.epub")},
    })
    before = service.storage.acquisitions(provider_id="manual")
    def changed_during_hash(_service, _account, _row, _path):
        broker.set_bnf_auto_download(sid, False)
        return True

    monkeypatch.setattr(auto, "_receipt_local_file_verified", changed_during_hash)
    monkeypatch.setattr(
        session, "download_digital",
        lambda *a, **kw: pytest.fail("unexpected GET while inspecting old receipt"),
    )
    try:
        assert auto.materialize(service, account, loan) == {
            "status": "attention_required", "code": "bnf_loan_scope_unproven",
        }
        assert service.storage.acquisitions(provider_id="manual") == before
        assert not downloaded
    finally:
        service.close()


def test_opt_in_revision_changes_even_if_final_boolean_returns_to_true(
    connected_core,
):
    service, session, account, loan, _state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    key = auto.policy_key(account)
    assert broker.set_bnf_auto_download(sid, True)
    first = service.storage.metadata_get(key + ".revision")
    assert type(first) is str and len(first) == 32
    assert broker.set_bnf_auto_download(sid, False) is False
    second = service.storage.metadata_get(key + ".revision")
    assert second != first
    assert broker.set_bnf_auto_download(sid, True)
    third = service.storage.metadata_get(key + ".revision")
    assert third not in {first, second}
    assert auto.enabled(service, account)
    assert downloaded == []
    service.close()
