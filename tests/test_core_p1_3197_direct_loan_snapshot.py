"""Core #3197: direct loan consultation opts in to the atomic snapshot once.

Legacy installed plugins retain loans(account); no provider HTTP, ADEPT or
rights reconciliation is invoked in these synthetic tests.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.providers import ProviderConfigurationError
from cifereca.services import _loan_cache_key
from tests.test_core_p1_3119_3122_opening_typed_inventory import (
    account, attached, loan, snapshot,
)
from tests.test_sync_state_contract import _service


def test_direct_account_reader_calls_snapshot_once_not_legacy(tmp_path):
    service = _service(tmp_path)
    try:
        a = account()
        service.accounts = [a]
        session, calls = attached(service, a)
        rows = service._loans_for_account(a, force=True)
        assert calls == [a]
        assert len(rows) == 1 and rows[0].id == "loan-one"
        assert len(service.storage.cache_get("account", _loan_cache_key(a))) == 1
        assert service.storage.cache_get("account", _loan_cache_key(a))[0]["raw"] == {}
        # A later unforced read is the positive, account-scoped local cache.
        assert [r.id for r in service._loans_for_account(a)] == ["loan-one"]
        assert calls == [a]
        assert session.last_loan_errors  # v1 side channel cannot void a typed read.
        assert service.storage.entitlements(provider_id="paris") == []
    finally:
        service.close()


def test_typed_partial_positive_is_not_promoted_to_negative_evidence(tmp_path):
    service = _service(tmp_path)
    try:
        a = account("bnf-pret", credential="reader-card", borrower="reader")
        service.accounts = [a]
        session, calls = attached(service, a, status="partial")
        before = session.last_loan_errors
        rows = service._loans_for_account(a, force=True)
        assert calls == [a] and len(rows) == 1
        assert before == session.last_loan_errors
        assert service.storage.entitlements(provider_id="bnf-pret") == []
        # A partial snapshot may cache positive observations, never remove
        # loans or prove the empty upstream was exhaustive.
        assert len(service.storage.cache_get("account", _loan_cache_key(a))) == 1
    finally:
        service.close()


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_typed_read_never_falls_back_to_legacy_or_updates_cache(
    tmp_path, status,
):
    service = _service(tmp_path)
    try:
        a = account()
        service.accounts = [a]
        _, calls = attached(service, a, status=status, observed=[])
        with pytest.raises(ProviderConfigurationError, match="loan_inventory_read_unavailable"):
            service._loans_for_account(a, force=True)
        assert calls == [a]
        assert service.storage.cache_get("account", _loan_cache_key(a)) is None
        assert service.storage.entitlements(provider_id="paris") == []
    finally:
        service.close()


@pytest.mark.parametrize("change", ["epoch", "session_swap", "connection_closed", "credential"])
def test_typed_consultation_rejects_changed_source_before_cache(tmp_path, change):
    service = _service(tmp_path)
    try:
        a = account()
        service.accounts = [a]
        session, calls = attached(service, a)

        def attack(requested):
            calls.append(requested)
            result = snapshot(a, [loan(a)])
            if change == "epoch":
                service._source_context_epoch += 1
            elif change == "session_swap":
                service._sessions[service._session_key(a.provider_id, a.credential_id)] = NS(
                    provider_id=a.provider_id, credential_id=a.credential_id,
                    connected=True, capabilities=session.capabilities,
                )
            elif change == "connection_closed":
                session.connected = False
            elif change == "credential":
                session.credential_id = "other-reader"
            return result

        session.loan_inventory_snapshot = attack
        with pytest.raises(
            ProviderConfigurationError, match="loan_inventory_(read_unavailable|scope_changed)",
        ):
            service._loans_for_account(a, force=True)
        assert calls == [a]
        assert service.storage.cache_get("account", _loan_cache_key(a)) is None
        assert service.storage.entitlements(provider_id="paris") == []
    finally:
        service.close()


def test_legacy_plugin_without_snapshot_keeps_exact_one_read(tmp_path):
    service = _service(tmp_path)
    try:
        a = account()
        service.accounts = [a]
        calls = []
        session = NS(
            provider_id=a.provider_id, credential_id=a.credential_id,
            connected=True, capabilities=NS(loans=True),
        )

        def loans(requested):
            calls.append(requested)
            return [loan(a)]

        session.loans = loans
        service._sessions[service._session_key(a.provider_id, a.credential_id)] = session
        results = service._loans_for_account(a, force=True)
        assert calls == [a]
        assert len(results) == 1
        assert service.storage.cache_get("account", _loan_cache_key(a)) is not None
    finally:
        service.close()


def test_direct_typed_reader_property_is_not_evaluated_or_retried(tmp_path):
    # Opening/source Sync already use static probing. The direct reader must
    # offer the same no-getter-I/O guarantee before choosing typed vs v1.
    from tests.test_core_p1_3197_static_sync_probe import _poison_snapshot

    service = _service(tmp_path)
    try:
        selected = account()
        service.accounts = [selected]
        session, typed_calls = attached(service, selected)
        session.loans = lambda _account: pytest.fail("unexpected v1 provider GET")
        probes = []
        hostile = _poison_snapshot(session, probes)
        service._sessions[service._session_key("paris", "card")] = hostile

        with pytest.raises(ProviderConfigurationError, match="loan_inventory_read_unavailable"):
            service._loans_for_account(selected, force=True)
        assert probes == []
        assert typed_calls == []
        assert service.storage.cache_get("account", _loan_cache_key(selected)) is None
        assert service.storage.entitlements(provider_id="paris") == []
        assert service.storage.acquisitions() == []
    finally:
        service.close()
