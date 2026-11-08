"""#2055: startup must never confer an SSO identity or a phantom connection.

Tests are synthetic and offline: no browser, SAML, private trace, or network.
"""
import logging

import pytest

from cifereca.core import provider_sessions
from tests.test_core_p0_1143_provider_connect_privacy import _rig, SECRET


@pytest.mark.parametrize("singleton", [False, True])
@pytest.mark.parametrize("reported_state", [False, None, 1, "true"])
def test_normal_return_without_explicit_connected_true_is_not_success(singleton, reported_state, caplog):
    service, session, _plugin = _rig(singleton=singleton, succeeds=True)

    def misleading_connect():
        session.connected = reported_state
        return [] if singleton else [service._local_account()]

    session.connect.side_effect = misleading_connect
    with caplog.at_level(logging.INFO, logger="cifereca"):
        output = provider_sessions.connect_configured(service, logger=logging.getLogger("cifereca"))

    assert output["connected"] == []
    assert len(output["errors"]) == 1
    assert service._sessions == {}
    session.close.assert_called_once()
    assert "EVENT provider connection success" not in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize("singleton", [False, True])
def test_automatic_startup_never_instantiates_interactive_provider(singleton, caplog):
    service, session, plugin = _rig(singleton=singleton, succeeds=True)
    plugin.descriptor.interactive_authentication = True
    with caplog.at_level(logging.INFO, logger="cifereca"):
        output = provider_sessions.connect_configured(service, logger=logging.getLogger("cifereca"))

    assert output["connected"] == []
    assert output["errors"] == {}
    assert not service._sessions
    plugin.create_session.assert_not_called()
    session.connect.assert_not_called()
    assert "EVENT provider connection success" not in caplog.text
    assert "EVENT provider connection failure" not in caplog.text
    assert SECRET not in caplog.text


def test_normal_provider_still_connects_and_is_registered():
    service, session, plugin = _rig(succeeds=True)
    output = provider_sessions.connect_configured(service, logger=logging.getLogger("cifereca"))
    assert output["connected"] == ["provider.test:" + SECRET]
    assert len(output["accounts"]) == 1
    assert session.connected is True
    assert service._sessions["provider.test:" + SECRET] is session


@pytest.mark.parametrize(("option", "declared", "accepted"), [
    ("on", True, True),
    ("off", True, False),
    ("true", True, False),
    ("ON", True, False),
    (True, True, False),
    (1, True, False),
    (None, True, False),
    ("on", False, False),
])
def test_interactive_background_auth_requires_exact_declared_saved_consent(
    option, declared, accepted,
):
    """#3691: another user's SSO cannot be opened by a generic startup sweep."""
    from types import SimpleNamespace as NS

    service, session, plugin = _rig(succeeds=True)
    plugin.descriptor.interactive_authentication = True
    plugin.descriptor.background_authentication_option_key = "authenticated_sync"
    plugin.descriptor.account_options = (
        (NS(key="authenticated_sync"),) if declared else ()
    )
    row = {
        "id": SECRET, "provider": "provider.test",
        "identity_id": "identity-private", "enabled": True,
        "credentials": {"password": SECRET},
        "options": {"authenticated_sync": option},
    }
    service.config.provider_accounts = lambda **_kwargs: [row]
    result = provider_sessions.connect_configured(
        service, logger=logging.getLogger("cifereca"))
    if accepted:
        assert result["connected"] == ["provider.test:" + SECRET]
        session.connect.assert_called_once()
    else:
        assert result["connected"] == [] and result["errors"] == {}
        assert not service._sessions
        plugin.create_session.assert_not_called()
        session.connect.assert_not_called()


def test_interactive_background_auth_does_not_accept_undeclared_or_non_dict_option():
    from types import SimpleNamespace as NS
    descriptor = NS(
        background_authentication_option_key="authenticated_sync",
        account_options=(NS(key="authenticated_sync"),),
    )
    for row in ({}, {"options": None}, {"options": "on"},
                {"options": {"authenticated_sync": "yes"}},
                {"options": {"authenticated_sync": True}}):
        assert not provider_sessions._interactive_background_opted_in(
            descriptor, row)
    for flag in (False, "true", 1, None):
        assert not provider_sessions._interactive_background_opted_in(
            descriptor, {"enabled": flag, "options": {"authenticated_sync": "on"}})
    descriptor.background_authentication_option_key = None
    assert not provider_sessions._interactive_background_opted_in(
        descriptor, {"options": {"authenticated_sync": "on"}})
