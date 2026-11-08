"""#3691: explicit diagnostic cannot accidentally reconnect sources."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from cifereca.core.provider_explicit_probe import (
    ExplicitProbeRefused, run_explicit_provider_probe,
)


def rig(*, interactive=True, enabled=True):
    session = Mock()
    session.connected = False
    session.connect.side_effect = ValueError("PRIVATE-TOKEN-NEVER-IN-OUTPUT")
    plugin = SimpleNamespace(
        descriptor=SimpleNamespace(
            state="ready", interactive_authentication=interactive,
            display_name="Synthetic"),
        create_session=Mock(return_value=session),
    )
    row = {
        "identity_id": "alice", "provider": "synthetic",
        "id": "credential", "enabled": enabled,
        "credentials": {"username": "PRIVATE-USER", "password": "PRIVATE-PASS"},
        "options": {"experimental_http_probe": "on"},
    }
    service = SimpleNamespace(
        config=SimpleNamespace(provider_accounts=lambda enabled_only=False:
                               [row] if enabled or not enabled_only else []),
        registry=SimpleNamespace(maybe_get=lambda _: plugin),
        storage=object(), diagnostics=object(),
        connect_configured=Mock(side_effect=AssertionError("global sweep forbidden")),
        _sessions={},
    )
    return service, plugin, session


def test_selected_interactive_account_gets_one_ephemeral_attempt():
    service, plugin, session = rig()
    result = run_explicit_provider_probe(
        service, identity_id="alice", provider_id="synthetic",
        credential_id="credential", user_consent=True,
    )
    assert result.status == "observed"
    assert not result.connected
    assert result.reason == "provider_diagnostic_completed_unverified"
    plugin.create_session.assert_called_once()
    assert plugin.create_session.call_args.kwargs["credentials"]["password"] == "PRIVATE-PASS"
    session.connect.assert_called_once()
    session.close.assert_called_once()
    service.connect_configured.assert_not_called()
    assert service._sessions == {}
    assert "PRIVATE" not in repr(result)


@pytest.mark.parametrize("consent", [False, None, "yes", 1])
def test_no_user_consent_means_no_session_or_network(consent):
    service, plugin, session = rig()
    with pytest.raises(ExplicitProbeRefused, match="consent"):
        run_explicit_provider_probe(
            service, identity_id="alice", provider_id="synthetic",
            credential_id="credential", user_consent=consent)
    plugin.create_session.assert_not_called()
    session.connect.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"identity_id": "bob", "provider_id": "synthetic", "credential_id": "credential"},
    {"identity_id": "alice", "provider_id": "synthetic", "credential_id": "other"},
    {"identity_id": "alice", "provider_id": "other", "credential_id": "credential"},
])
def test_wrong_account_scope_rejected_before_session(kwargs):
    service, plugin, session = rig()
    with pytest.raises(ExplicitProbeRefused, match="not_unique"):
        run_explicit_provider_probe(service, user_consent=True, **kwargs)
    plugin.create_session.assert_not_called()


def test_noninteractive_provider_cannot_use_interactive_diagnostic():
    service, plugin, session = rig(interactive=False)
    with pytest.raises(ExplicitProbeRefused, match="not_interactive"):
        run_explicit_provider_probe(
            service, identity_id="alice", provider_id="synthetic",
            credential_id="credential", user_consent=True)
    plugin.create_session.assert_not_called()


def test_disabled_account_not_queried():
    service, plugin, session = rig(enabled=False)
    with pytest.raises(ExplicitProbeRefused, match="not_unique"):
        run_explicit_provider_probe(
            service, identity_id="alice", provider_id="synthetic",
            credential_id="credential", user_consent=True)
    plugin.create_session.assert_not_called()
