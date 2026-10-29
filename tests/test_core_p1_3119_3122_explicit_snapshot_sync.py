"""P1 #3119/#3122: explicit source Sync consumes an atomic provider snapshot.

All provider responses are synthetic. No network, acquisition, ACSM or return.
The legacy source API is covered independently in #2975 parity tests.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core import sync_coordination
from cifereca.models import Loan
from cifereca.plugin_api import (
    Cancelled, LoanInventoryScope, LoanInventorySnapshot, LoanBranchCoverage,
)
from cifereca.providers import ProviderConfigurationError
from cifereca.services import _loan_cache_key
from tests.test_core_p0_2975_manual_source_loan_parity import (
    _fixture, _loan, _right,
)


def _snapshot(account, rows, *, status="success"):
    return LoanInventorySnapshot(
        scope=LoanInventoryScope.for_account(account),
        loans=tuple(rows), status=status,
        branches=(
            LoanBranchCoverage(
                upstream="dilicom", medium="digital",
                completeness="unknown",
            ),
            LoanBranchCoverage(
                upstream="syracuse", medium="physical",
                completeness="partial" if status == "partial" else "unknown",
                reason_code="branch_unavailable" if status == "partial" else None,
            ),
        ),
        reason_code="branch_unavailable" if status == "partial" else None,
    )


def test_typed_source_sync_reads_once_preserves_positive_right_and_refreshes_cache(
    tmp_path, monkeypatch,
):
    service, primary, _, _, _, session = _fixture(tmp_path, monkeypatch)
    calls = []
    try:
        def typed(account):
            calls.append(account)
            return _snapshot(account, [_loan()])

        session.loan_inventory_snapshot = typed
        session.loans = lambda _account: pytest.fail("legacy second GET forbidden")
        outcome = service.synchronize_account_source(primary)
        assert len(calls) == 1 and calls[0] is primary
        assert outcome["loans"] == 1 and "status" not in outcome
        assert _right(service, "primary", "edition")["state"] == "active"
        cache = service.storage.cache_get("account", _loan_cache_key(primary))
        assert len(cache) == 1 and cache[0]["identity_id"] == "default"
        assert cache[0]["raw"] == {}
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
    finally:
        service.close()


def test_typed_partial_empty_does_not_close_other_loan_or_claim_success(
    tmp_path, monkeypatch,
):
    service, primary, _, _, _, session = _fixture(tmp_path, monkeypatch)
    try:
        service._persist_active_loans("default", [_loan(item="earlier")])
        session.loan_inventory_snapshot = lambda account: _snapshot(
            account, [], status="partial",
        )
        session.last_loan_errors = []  # Stale legacy side-channel disagrees.
        outcome = service.synchronize_account_source(primary)
        assert outcome["status"] == "partial" and outcome["loans"] == 0
        assert _right(service, "primary", "earlier")["state"] == "active"
        assert service.storage.acquisitions() == []
    finally:
        service.close()


@pytest.mark.parametrize("fault", [
    "provider_failed", "provider_cancelled", "foreign_scope",
    "session_replaced", "epoch_changed", "source_disabled",
    "account_replaced", "cancel_after_get",
])
def test_typed_source_never_persists_unverified_or_stale_batch(
    tmp_path, monkeypatch, fault,
):
    service, primary, _, _, _, session = _fixture(tmp_path, monkeypatch)
    calls = []
    try:
        def typed(account):
            calls.append("get")
            positive = _snapshot(account, [_loan()])
            if fault == "provider_failed":
                return LoanInventorySnapshot(
                    scope=LoanInventoryScope.for_account(account),
                    loans=(), status="failed", reason_code="read_failed",
                )
            if fault == "provider_cancelled":
                return LoanInventorySnapshot(
                    scope=LoanInventoryScope.for_account(account),
                    loans=(), status="cancelled", reason_code="cancelled",
                )
            if fault == "foreign_scope":
                from cifereca.models import Account
                wrong = Account(
                    provider_id="paris", credential_id="other",
                    identity_id="default", unique_id="",
                )
                return LoanInventorySnapshot(
                    scope=LoanInventoryScope.for_account(wrong),
                    loans=(_loan(credential="other"),),
                    status="success",
                )
            if fault == "session_replaced":
                service._sessions[service._session_key("paris", "cred")] = NS(
                    provider_id="paris", credential_id="cred", connected=True,
                    capabilities=session.capabilities,
                )
            elif fault == "epoch_changed":
                service._source_context_epoch += 1
            elif fault == "source_disabled":
                service.storage.metadata_set(
                    "sync.source.enabled." + service.sync_source_id(primary), "0",
                )
            elif fault == "account_replaced":
                from dataclasses import replace
                service.accounts = [replace(primary)]
            return positive

        session.loan_inventory_snapshot = typed
        session.loans = lambda _account: pytest.fail("no duplicate legacy GET")
        control = None
        if fault == "cancel_after_get":
            def check():
                if calls:
                    raise RuntimeError("synthetic cancellation")
            control = NS(check_cancelled=check)

        expected = (Cancelled if fault == "provider_cancelled"
                    else (ProviderConfigurationError, RuntimeError))
        with pytest.raises(expected):
            service.synchronize_account_source(primary, control=control)
        assert calls == ["get"]
        assert not service.storage.entitlements(provider_id="paris")
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
        assert service.storage.cache_get("account", _loan_cache_key(primary)) is None
    finally:
        service.close()


def test_cancelled_typed_source_is_not_recorded_as_failed(tmp_path, monkeypatch):
    service, primary, _, _, _, session = _fixture(tmp_path, monkeypatch)
    action_events = []
    try:
        monkeypatch.setattr(
            service, "_record_action",
            lambda identity, action, outcome, **kwargs:
                action_events.append((identity, action, outcome, kwargs)),
        )
        session.loan_inventory_snapshot = lambda account: LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(account),
            loans=(), status="cancelled", reason_code="cancelled",
        )
        session.loans = lambda _: pytest.fail("cancelled typed read must not retry v1")
        with pytest.raises(Cancelled, match="loan_inventory_cancelled"):
            service.synchronize_account_source(primary)
        assert action_events == [(
            primary.identity_id, "synchronization", "cancelled",
            {
                "provider_id": primary.provider_id,
                "facts": {"deep": False, "error": "source_sync_cancelled"},
            },
        )]
        assert service.storage.entitlements(provider_id="paris") == []
        assert service.storage.cache_get("account", _loan_cache_key(primary)) is None
    finally:
        service.close()
