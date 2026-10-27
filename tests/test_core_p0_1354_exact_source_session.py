"""P0 #1354: an exact source credential must never borrow another live session.

No provider network, login or action is performed. Other consumers of the
existing session resolver (loan actions, Kobo, Numilog) share this invariant.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.main_window import MainWindow
from cifereca.providers import ProviderConfigurationError
from tests.test_sync_state_contract import _service


def _sessions(service):
    def make(credential):
        return NS(
            provider_id="paris", credential_id=credential, connected=True,
            capabilities=NS(borrow=True), accounts=[],
            close=lambda: None,
        )
    a, b = make("account-A"), make("account-B")
    service._sessions[service._session_key("paris", "account-A")] = a
    service._sessions[service._session_key("paris", "account-B")] = b
    return a, b


def test_exact_credential_selects_only_own_session(tmp_path):
    service = _service(tmp_path)
    try:
        a, b = _sessions(service)
        assert service._session_for_source("paris", "account-A") is a
        assert service._session_for_source("paris", "account-B") is b
        # The legacy NO-credential caller retains its existing behavior.
        assert service._session_for_source("paris") is a
    finally:
        service.close()


@pytest.mark.parametrize("missing", ["account-B", "", "removed-credential"])
def test_missing_explicit_credential_cannot_fall_back_to_live_sibling(
    tmp_path, missing,
):
    service = _service(tmp_path)
    try:
        a, _b = _sessions(service)
        service._sessions.pop(service._session_key("paris", "account-B"))
        with pytest.raises(ProviderConfigurationError, match="Provider non connecté"):
            service._session_for_source("paris", missing)
        # Existing A must remain intact and no new session is registered.
        assert service._session_for_source("paris", "account-A") is a
        assert len(service._sessions) == 1
    finally:
        service.close()


def test_search_borrow_action_refuses_stale_B_when_A_connected(tmp_path):
    service = _service(tmp_path)
    try:
        a, _b = _sessions(service)
        service._sessions.pop(service._session_key("paris", "account-B"))
        ui = NS(service=service)
        item = NS(source_provider="paris", source_credential="account-B")
        assert MainWindow.can_borrow_item(ui, item) is False
        with pytest.raises(ProviderConfigurationError):
            MainWindow._eligible_borrow_accounts(ui, item)
        assert service._session_for_source("paris", "account-A") is a
    finally:
        service.close()


def test_session_credential_rebind_A_B_A_never_revives_missing_B(tmp_path):
    service = _service(tmp_path)
    try:
        a, b = _sessions(service)
        key_b = service._session_key("paris", "account-B")
        service._sessions.pop(key_b)
        for _ in range(3):
            with pytest.raises(ProviderConfigurationError):
                service._session_for_source("paris", "account-B")
            assert service._session_for_source("paris", "account-A") is a
        service._sessions[key_b] = b
        assert service._session_for_source("paris", "account-B") is b
    finally:
        service.close()
