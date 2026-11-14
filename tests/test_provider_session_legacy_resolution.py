"""#5975 / #5851: never resolve a provider session by implicit credential.

No real plugin, provider transport or network is involved.
"""
from types import SimpleNamespace

import pytest

from cifereca.core.provider_sessions import _session_for_source
from cifereca.providers import ProviderConfigurationError


def _facade(*sessions):
    return SimpleNamespace(
        _sessions={(s.provider_id, s.credential_id): s for s in sessions},
        _session_key=lambda provider, credential: (provider, credential),
        _provider_enabled_for_identity=lambda provider: True,
    )


def _session(provider, credential, connected=True):
    return SimpleNamespace(
        provider_id=provider, credential_id=credential, connected=connected,
    )


def test_source_lookup_requires_explicit_credential_with_multiple_live_accounts():
    first = _session("synthetic", "alice")
    second = _session("synthetic", "bob")
    facade = _facade(first, second)

    with pytest.raises(TypeError):
        _session_for_source(facade, "synthetic")
    assert _session_for_source(facade, "synthetic", "alice") is first
    assert _session_for_source(facade, "synthetic", "bob") is second


def test_single_remaining_live_account_never_authorizes_provider_wide_lookup():
    first = _session("synthetic", "alice", connected=False)
    second = _session("synthetic", "bob")
    facade = _facade(first, second)
    with pytest.raises(TypeError):
        _session_for_source(facade, "synthetic")
    second.connected = False
    with pytest.raises(TypeError):
        _session_for_source(facade, "synthetic")
    # Exact lookup is still not a connected-state check: callers must recheck.
    assert _session_for_source(facade, "synthetic", "alice") is first


def test_removed_or_disconnected_B_never_falls_back_to_A():
    first = _session("synthetic", "alice")
    second = _session("synthetic", "bob", connected=False)
    facade = _facade(first, second)
    assert _session_for_source(facade, "synthetic", "bob") is second
    del facade._sessions["synthetic", "bob"]
    with pytest.raises(ProviderConfigurationError, match="Provider non connecté"):
        _session_for_source(facade, "synthetic", "bob")
    assert _session_for_source(facade, "synthetic", "alice") is first


@pytest.mark.parametrize("bad", [None, False, 0, object(), []])
def test_malformed_or_missing_credential_is_not_wildcard(bad):
    facade = _facade(_session("synthetic", "alice"))
    with pytest.raises(ProviderConfigurationError, match="source_session_scope_invalid"):
        _session_for_source(facade, "synthetic", bad)


def test_explicit_empty_native_credential_is_an_exact_key():
    empty = _session("synthetic", "")
    other = _session("synthetic", "alice")
    facade = _facade(empty, other)
    assert _session_for_source(facade, "synthetic", "") is empty
    assert _session_for_source(facade, "synthetic", "alice") is other
    with pytest.raises(ProviderConfigurationError, match="source_session_scope_invalid"):
        _session_for_source(facade, "", "")
