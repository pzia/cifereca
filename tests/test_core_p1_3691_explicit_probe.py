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


def test_one_click_can_test_an_off_account_without_enabling_background_sync():
    """One-shot consent is not a write to the persisted source options."""
    service, plugin, session = rig()
    plugin.descriptor.background_authentication_option_key = "authenticated_sync"
    plugin.descriptor.account_options = (SimpleNamespace(key="authenticated_sync"),)
    persisted = {
        "id": "credential", "provider": "synthetic", "identity_id": "alice",
        "enabled": True, "credentials": {
            "username": "PRIVATE-USER", "password": "PRIVATE-PASS",
        },
        "options": {"authenticated_sync": "off", "experimental_http_probe": "off"},
    }
    service.config.provider_accounts = lambda enabled_only=False: [persisted]

    def connected_once():
        session.connected = True
        session.accounts = [
            SimpleNamespace(provider_id="synthetic", credential_id="credential")
        ]
        return session.accounts
    session.connect.side_effect = connected_once

    result = run_explicit_provider_probe(
        service, identity_id="alice", provider_id="synthetic",
        credential_id="credential", user_consent=True,
    )
    assert result.status == "connected_ephemeral"
    assert result.connected is True
    assert result.reason == "provider_connection_test_succeeded_ephemeral"
    assert plugin.create_session.call_args.kwargs["options"]["authenticated_sync"] == "on"
    assert persisted["options"]["authenticated_sync"] == "off"
    assert persisted["options"]["experimental_http_probe"] == "off"
    assert service._sessions == {}
    session.close.assert_called_once()
    assert "PRIVATE" not in repr(result)


def test_declared_background_key_missing_never_invents_one_shot_consent():
    service, plugin, session = rig()
    plugin.descriptor.background_authentication_option_key = "not_declared"
    plugin.descriptor.account_options = (SimpleNamespace(key="something_else"),)
    run_explicit_provider_probe(
        service, identity_id="alice", provider_id="synthetic",
        credential_id="credential", user_consent=True,
    )
    passed = plugin.create_session.call_args.kwargs["options"]
    assert "not_declared" not in passed
    assert passed == {"experimental_http_probe": "on"}
    session.close.assert_called_once()
