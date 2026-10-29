"""P1 #3119/#3122: typed Paris/BnF startup inventory uses one scoped read.

All sessions, loans and HTTP responses here are synthetic; no supplier GET,
return/borrow, ACSM or ADEPT work takes place.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import library_mutation
from cifereca.models import Account, Loan
from cifereca.plugin_api import (
    LoanBranchCoverage, LoanInventoryScope, LoanInventorySnapshot,
)
from cifereca.providers import ProviderConfigurationError
from cifereca.services import _loan_cache_key
from tests.test_core_p0_1346_opening_source_isolation import _state
from tests.test_core_p0_2975_manual_source_loan_parity import (
    _fixture as explicit_fixture, _loan as explicit_loan,
)
from tests.test_sync_state_contract import _service


def account(provider="paris", *, identity="default", credential="card", borrower=""):
    return Account(
        provider_id=provider, credential_id=credential,
        identity_id=identity, unique_id=borrower,
        is_primary=provider == "paris" and borrower == "",
    )


def loan(a, suffix="one"):
    return Loan(
        id="loan-" + suffix, provider="PNB", medium="digital",
        record_id="book-" + suffix, title="Synthetic " + suffix,
        source_provider=a.provider_id, source_credential=a.credential_id,
        source_account_id=("primary" if a.provider_id == "paris"
                           and a.unique_id == "" else a.unique_id),
        identity_id=a.identity_id, due_at="2099-01-01T00:00:00Z",
    )


def snapshot(a, rows=(), status="success"):
    return LoanInventorySnapshot(
        scope=LoanInventoryScope.for_account(a),
        loans=tuple(rows), status=status,
        branches=(
            LoanBranchCoverage(
                upstream="bnf_html" if a.provider_id == "bnf-pret" else "dilicom",
                medium="digital",
                completeness="partial" if status == "partial" else "unknown",
                reason_code="inventory_not_exhaustive" if status == "partial" else None,
            ),
        ),
        reason_code=("inventory_not_exhaustive" if status == "partial"
                     else "read_failed" if status == "failed"
                     else "cancelled" if status == "cancelled" else None),
    )


def attached(service, a, *, observed=None, status="success"):
    calls = []
    state = NS(
        provider_id=a.provider_id, credential_id=a.credential_id,
        connected=True, accounts=[a],
        capabilities=NS(loans=True, digital_download=False, personal_library=False),
        last_loan_errors=[{"untrusted": "stale error with token=NEVER_EXPORT"}],
    )

    def reader(selected):
        calls.append(selected)
        return snapshot(a, [loan(a)] if observed is None else observed, status=status)

    state.loan_inventory_snapshot = reader
    state.loans = lambda _a: pytest.fail("legacy duplicate provider read")
    service._sessions[service._session_key(a.provider_id, a.credential_id)] = state
    return state, calls


def result_for(summary, identity="default"):
    return next(row for row in summary["identities"]
                if row["identity_id"] == identity)


def test_opening_consumes_one_paris_snapshot_without_legacy_call_and_caches_after_commit(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        a = account()
        service.accounts = [a]
        _session, calls = attached(service, a)
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda *_args, **_kwargs: pytest.fail("no second GET"))
        monkeypatch.setattr(service, "_acquire_active_loan_automatically",
                            lambda _row: pytest.fail("no acquisition without opt in"))
        result = service.opening_sync()
        assert calls == [a]
        assert result_for(result)["status"] == "success"
        assert _state(service, a)["status"] == "success"
        rights = service.storage.entitlements(provider_id="paris")
        assert len(rights) == 1 and rights[0]["state"] == "active"
        assert rights[0]["account_id"] == "primary"
        cache = service.storage.cache_get("account", _loan_cache_key(a))
        assert len(cache) == 1 and cache[0]["raw"] == {}
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
    finally:
        service.close()


def test_bnf_empty_partial_and_paris_positive_are_independent_on_startup(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        paris = account()
        bnf = account("bnf-pret", credential="bnf-card", borrower="reader")
        service.accounts = [paris, bnf]
        _paris, pcalls = attached(service, paris)
        _bnf, bcalls = attached(service, bnf, observed=[], status="partial")
        # Previously confirmed BnF loan must remain active on empty HTML.
        service._persist_active_loans("default", [loan(bnf, "historical")])
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda *_args, **_kwargs: pytest.fail("no legacy read"))
        summary = service.opening_sync()
        assert pcalls == [paris] and bcalls == [bnf]
        assert result_for(summary)["status"] == "partial"
        assert _state(service, paris)["status"] == "success"
        assert _state(service, bnf)["status"] == "partial"
        assert len(service.storage.entitlements(provider_id="paris")) == 1
        assert next(x for x in service.storage.entitlements(provider_id="bnf-pret")
                    if x["loan_id"] == "loan-historical")["state"] == "active"
        assert not service.storage.acquisitions()
    finally:
        service.close()


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_opening_failed_or_cancelled_typed_read_cannot_commit_or_cache(
    tmp_path, status,
):
    service = _service(tmp_path)
    try:
        a = account()
        service.accounts = [a]
        _, calls = attached(service, a, observed=[], status=status)
        summary = service.opening_sync()
        assert calls == [a]
        assert result_for(summary)["status"] == "failed"
        assert _state(service, a)["status"] == "failed"
        assert service.storage.entitlements(provider_id="paris") == []
        assert service.storage.cache_get("account", _loan_cache_key(a)) is None
    finally:
        service.close()


@pytest.mark.parametrize("fault", [
    "epoch", "session_swapped", "source_disabled", "account_replaced",
    "scope_wrong", "session_closed", "credential_mutated", "borrower_revoked",
])
def test_opening_typed_reader_change_during_network_discards_positive_batch(
    tmp_path, fault,
):
    service = _service(tmp_path)
    try:
        a = account()
        service.accounts = [a]
        session, calls = attached(service, a)

        def attack(selected):
            calls.append(selected)
            positive = snapshot(a, [loan(a)])
            if fault == "epoch":
                service._source_context_epoch += 1
            elif fault == "session_swapped":
                service._sessions[service._session_key(a.provider_id, a.credential_id)] = NS(
                    provider_id=a.provider_id, credential_id=a.credential_id,
                    connected=True, capabilities=session.capabilities,
                )
            elif fault == "source_disabled":
                service.storage.metadata_set(
                    "sync.source.enabled." + service.sync_source_id(a), "0",
                )
            elif fault == "account_replaced":
                service.accounts = [replace(a)]
            elif fault == "session_closed":
                session.connected = False
            elif fault == "credential_mutated":
                session.credential_id = "unbound"
            elif fault == "borrower_revoked":
                session.accounts = []
            elif fault == "scope_wrong":
                wrong = replace(a, credential_id="other")
                return snapshot(wrong, [loan(wrong)])
            return positive

        session.loan_inventory_snapshot = attack
        result = service.opening_sync()
        assert calls == [a]
        assert result_for(result)["status"] == "failed"
        assert service.storage.entitlements(provider_id="paris") == []
        assert service.storage.cache_get("account", _loan_cache_key(a)) is None
        assert not service.storage.acquisitions()
    finally:
        service.close()


def test_earlier_typed_scope_rechecked_after_second_account_read(tmp_path):
    service = _service(tmp_path)
    try:
        first = account(credential="first", borrower="reader-1")
        second = account(credential="second", borrower="reader-2")
        service.accounts = [first, second]
        first_session, first_calls = attached(service, first)
        second_session, second_calls = attached(service, second)

        def second_read(a):
            second_calls.append(a)
            service._sessions[service._session_key(first.provider_id, first.credential_id)] = NS(
                provider_id=first.provider_id, credential_id=first.credential_id,
                connected=True, capabilities=first_session.capabilities,
            )
            return snapshot(a, [loan(a, "second")])

        second_session.loan_inventory_snapshot = second_read
        summary = service.opening_sync()
        assert first_calls == [first] and second_calls == [second]
        assert result_for(summary)["status"] == "failed"
        assert service.storage.entitlements(provider_id="paris") == []
        assert service.storage.cache_get("account", _loan_cache_key(first)) is None
        assert service.storage.cache_get("account", _loan_cache_key(second)) is None
    finally:
        service.close()


def test_explicit_typed_cache_follows_successful_rights_commit_only(
    tmp_path, monkeypatch,
):
    service, primary, _, _, _, session = explicit_fixture(tmp_path, monkeypatch)
    try:
        session.loan_inventory_snapshot = lambda a: snapshot(a, [explicit_loan()])
        session.loans = lambda _a: pytest.fail("no legacy duplicate GET")

        def rejected(*_args, **_kwargs):
            raise ProviderConfigurationError("synthetic_rights_commit_rejected")

        monkeypatch.setattr(library_mutation, "_persist_active_loans", rejected)
        with pytest.raises(ProviderConfigurationError, match="synthetic_rights_commit_rejected"):
            service.synchronize_account_source(primary)
        assert service.storage.cache_get("account", _loan_cache_key(primary)) is None
        assert service.storage.entitlements(provider_id="paris") == []
    finally:
        service.close()
