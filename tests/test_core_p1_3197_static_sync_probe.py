"""#3197: choosing a typed inventory must not call a provider getter.

No HTTP, rights mutation, loan POST, ADEPT, or acquisition; only local Core
and synthetic source state. Both explicit and opening sync must fail closed
when a provider declares a hostile snapshot property.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_2975_manual_source_loan_parity import _fixture
from tests.test_core_p1_3119_3122_opening_typed_inventory import (
    account, attached, result_for,
)
from tests.test_sync_state_contract import _service


def _poison_snapshot(session, calls):
    class Poison(NS):
        @property
        def loan_inventory_snapshot(self):
            calls.append("provider_property_network_request")
            raise AssertionError("never invoke a getter while probing typed loans")

    poison = Poison()
    vars(poison).update({
        key: value for key, value in vars(session).items()
        if key != "loan_inventory_snapshot"
    })
    return poison


def test_explicit_source_malformed_typed_reader_does_not_get_or_fallback(
    tmp_path, monkeypatch,
):
    service, primary, _, _, _, session = _fixture(tmp_path, monkeypatch)
    probes = []
    session.loans = lambda _account: pytest.fail("no legacy fallback GET")
    hostile = _poison_snapshot(session, probes)
    service._sessions[service._session_key("paris", "cred")] = hostile
    try:
        with pytest.raises(ProviderConfigurationError, match="loan_inventory_read_unavailable"):
            service.synchronize_account_source(primary)
        assert probes == []
        assert service.storage.entitlements(provider_id="paris") == []
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
    finally:
        service.close()


def test_opening_malformed_typed_reader_fails_source_without_second_get(tmp_path):
    service = _service(tmp_path)
    try:
        selected = account()
        service.accounts = [selected]
        session, typed_calls = attached(service, selected)
        session.loans = lambda _account: pytest.fail("no legacy fallback GET")
        probes = []
        hostile = _poison_snapshot(session, probes)
        service._sessions[service._session_key("paris", "card")] = hostile
        summary = service.opening_sync()
        assert probes == [] and typed_calls == []
        assert result_for(summary)["status"] == "failed"
        assert service.storage.entitlements(provider_id="paris") == []
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
    finally:
        service.close()
