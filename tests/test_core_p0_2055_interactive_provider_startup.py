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
