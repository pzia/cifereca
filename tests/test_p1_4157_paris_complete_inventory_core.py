"""Actual Paris ListLoans -> Core inventory sync -> SQLite/FS, all synthetic.

No real provider account, ACSM, ADEPT or remote mutation. Core is unchanged.
"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/plugins/provider-paris"))
from cifereca_paris import ParisProviderSession
from cifereca.models import Account
from cifereca.core.sync_coordination import _synchronize_account_source_impl
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.brokers import IdentityBrokerImpl
from tests.test_sync_state_contract import _service
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, register, target, publish,
)


@pytest.mark.parametrize("failed_branch", [None, "DILICOM", "SYRACUSE"])
def test_real_empty_reader_ends_only_exact_loan_and_preserves_all_file_provenance(
    tmp_path, failed_branch,
):
    service = _service(tmp_path)
    session = ParisProviderSession(
        credential_id="paris-card", label="Paris", credentials={}, options={},
        config={}, diagnostics=NS(add=lambda *a, **k: None),
    )
    accounts = [Account(identity_id="default", provider_id="paris",
                        credential_id="paris-card", unique_id=name)
                for name in ("reader-A", "reader-B")]
    session._accounts = accounts
    session.connected = True
    session._enrich_digital_loan_metadata = lambda loan: None
    service.accounts = accounts
    service._sessions[service._session_key("paris", "paris-card")] = session
    state = {"empty": False, "failure": None}
    calls = []

    def handler(request):
        assert request.method == "GET"
        assert request.url.path.endswith("/ListLoans")
        branch = request.url.params["serviceCode"]
        borrower = request.url.params["userUniqueIdentifier"]
        calls.append((branch, borrower))
        if branch == state["failure"]:
            return httpx.Response(503)
        rows = [] if branch == "SYRACUSE" or (state["empty"] and borrower == "reader-A") else [{
            "Id": "loan-" + borrower, "RecordId": "edition-" + borrower,
            "Title": "Synthetic", "State": "active",
            "WhenBack": "/Date(4102444800000)/",
        }]
        return httpx.Response(200, json={"success": True, "d": {"Loans": rows}})

    session.client.client.close()
    session.client.client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    try:
        for account in accounts:
            register(service.storage, "default", "paris", "paris-card", account=account.unique_id)
            result = _synchronize_account_source_impl(service, account, inventory_only=True)
            assert result.get("status", "ok") == "ok" and result["loans"] == 1

        key, snapshot = new_source(
            "default", provider="paris", credential="paris-card", account="reader-A",
            branch="dilicom", edition="edition-reader-A", right="loan-reader-A", kind="loan",
        )
        path = target(tmp_path, "default")
        publish(service.storage, service.library_dir(), path, key, snapshot,
                adept_receipt_ref="synthetic-adept-origin")
        placements = service.storage.placements()
        with service.storage._connect() as db:
            origins = db.execute("SELECT * FROM origin_links ORDER BY origin_link_id").fetchall()

        state.update(empty=True, failure=failed_branch)
        result = _synchronize_account_source_impl(service, accounts[0], inventory_only=True)
        assert result.get("status", "ok") == ("ok" if failed_branch is None else "partial")
        states = {r["loan_id"]: r["state"] for r in service.storage.entitlements()}
        assert states["loan-reader-A"] == ("realized" if failed_branch is None else "active")
        assert states["loan-reader-B"] == "active"
        assert service.storage.placements() == placements
        assert path.read_bytes() == BYTES
        with service.storage._connect() as db:
            assert db.execute("SELECT * FROM origin_links ORDER BY origin_link_id").fetchall() == origins

        # Reappearance is a fresh positive state, with no borrow or file GET.
        state.update(empty=False, failure=None)
        _synchronize_account_source_impl(service, accounts[0], inventory_only=True)
        states = {r["loan_id"]: r["state"] for r in service.storage.entitlements()}
        assert states["loan-reader-A"] == states["loan-reader-B"] == "active"
        assert len(calls) == 8
        assert service.storage.placements() == placements
        assert path.read_bytes() == BYTES
    finally:
        service.close()


@pytest.mark.parametrize("present,unavailable,expected", [
    (True, False, "ok"), (False, False, "partial"), (True, True, "partial"),
])
def test_complete_remote_read_cannot_hide_missing_file_or_branch_failure(
    tmp_path, monkeypatch, present, unavailable, expected,
):
    service = _service(tmp_path)
    account = Account(identity_id="default", provider_id="paris",
                      credential_id="paris-card", unique_id="reader-A")
    register(service.storage, "default", "paris", "paris-card", account="reader-A")
    service.accounts = [account]
    session = ParisProviderSession(
        credential_id="paris-card", label="Paris", credentials={}, options={},
        config={}, diagnostics=NS(add=lambda *a, **k: None),
        identity_broker=IdentityBrokerImpl(service),
    )
    session.connected = True
    session._accounts = [account]
    session._enrich_digital_loan_metadata = lambda loan: None
    service._sessions[service._session_key("paris", "paris-card")] = session
    calls = []
    def handler(request):
        assert request.method == "GET" and request.url.path.endswith("/ListLoans")
        branch = request.url.params["serviceCode"]
        calls.append(branch)
        if unavailable and branch == "SYRACUSE":
            return httpx.Response(503)
        rows = [{"Id": "loan", "RecordId": "edition", "State": "active",
                 "Title": "Synthetic", "WhenBack": "/Date(4102444800000)/"}] if branch == "DILICOM" else []
        return httpx.Response(200, json={"success": True, "d": {"Loans": rows}})
    session.client.client.close()
    session.client.client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    for name in (
        "cifereca.core.sync_coordination.acquisition_lock_file",
        "cifereca.core.convergence_execution.acquisition_lock_file",
        "cifereca.core.canonical_recovery.acquisition_lock_file",
    ):
        monkeypatch.setattr(name, lambda: tmp_path / "acquisition.lock")
    try:
        if present:
            key, snapshot = new_source(
                "default", provider="paris", credential="paris-card", account="reader-A",
                branch="dilicom", edition="edition", right="loan", kind="loan",
            )
            path = target(tmp_path, "default")
            publish(service.storage, service.library_dir(), path, key, snapshot)
        before = service.storage.placements()
        result = synchronize_account_source(service, account)
        assert result.get("status", "ok") == expected
        assert result["canonical_rights"]["observed"] == 1
        assert result["canonical_rights"]["present_attested"] == int(present)
        assert result["canonical_rights"]["pending"] == int(not present)
        assert service.storage.placements() == before
        assert calls == ["DILICOM", "SYRACUSE", "DILICOM", "SYRACUSE"]
    finally:
        service.close()
