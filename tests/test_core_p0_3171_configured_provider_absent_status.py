"""#3171: enabled configured sources cannot disappear from opening-sync health.

All cases are local and synthetic. Interactive authentication stays an
intentional skip; no network, secrets, rights or consent are changed.
"""
import logging
from types import SimpleNamespace

import pytest

from cifereca.core import provider_sessions, sync_coordination
from tests.test_core_p0_1143_provider_connect_privacy import _rig, SECRET


@pytest.mark.parametrize("mode", ["missing", "not_ready"])
def test_unavailable_configured_provider_is_counted_by_identity(mode):
    state, session, plugin = _rig()
    state.identities = [SimpleNamespace(identity_id="identity-private")]
    if mode == "missing":
        state.registry.maybe_get = lambda _id: None
    else:
        plugin.descriptor.state = "disabled"
    result = provider_sessions.connect_configured(
        state, logger=logging.getLogger("cifereca"),
    )
    key = state._session_key("provider.test", SECRET)
    assert result["connected"] == []
    assert result["errors"][key]
    assert result["connection_failures"]["items"]
    assert result["accounts"] == [state.primary]
    assert sync_coordination._opening_failed_configured_connections(
        state, "identity-private",
    ) == 1
    assert sync_coordination._opening_failed_configured_connections(
        state, "other-identity",
    ) == 0
    session.connect.assert_not_called()
    plugin.create_session.assert_not_called()


def test_interactive_provider_is_skipped_without_failure_or_network():
    state, session, plugin = _rig()
    state.identities = [SimpleNamespace(identity_id="identity-private")]
    plugin.descriptor.interactive_authentication = True
    result = provider_sessions.connect_configured(
        state, logger=logging.getLogger("cifereca"),
    )
    assert result["errors"] == {}
    assert result["connection_failures"]["items"] == ()
    assert sync_coordination._opening_failed_configured_connections(
        state, "identity-private",
    ) == 0
    session.connect.assert_not_called()
    plugin.create_session.assert_not_called()
