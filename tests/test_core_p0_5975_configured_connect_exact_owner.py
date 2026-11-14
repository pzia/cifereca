"""#5975: configured private connection requires a persisted exact owner."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core import provider_sessions
from cifereca.models import Account


def _descriptor(*, anonymous=False):
    return NS(id="synthetic", state="ready",
              display_name="Synthetic", interactive_authentication=False,
              credential_fields=() if anonymous else (NS(key="token"),))


@pytest.mark.parametrize("identity", ["alice", "bob", "reader0"])
def test_typed_configured_identity_is_independent_of_gtk_navigation(identity):
    row = {"id": "c", "provider": "synthetic", "identity_id": identity}
    assert provider_sessions._configured_connection_identity(
        row, _descriptor(),
    ) == identity


@pytest.mark.parametrize("invalid", [
    None, "", " ", "alice ", " bob", False, 0, [], {}, "alice\\x00x",
    "alice\\r", "alice\\n", "a" * 257,
])
def test_private_missing_or_malformed_owner_is_never_implicit(invalid):
    row = {"id": "c", "provider": "synthetic", "identity_id": invalid}
    assert provider_sessions._configured_connection_identity(
        row, _descriptor(),
    ) is None


def test_synthetic_anonymous_public_keeps_neutral_namespace_without_gtk():
    row = {"id": "synthetic-public", "provider": "synthetic", "anonymous": True}
    assert provider_sessions._configured_connection_identity(
        row, _descriptor(anonymous=True),
    ) == "default"
    assert provider_sessions._configured_connection_identity(
        row, _descriptor(),
    ) is None


@pytest.mark.parametrize("selected", ["alice", "bob"])
def test_full_sweep_never_connects_private_unbound_row(monkeypatch, selected):
    from cifereca.core import diagnostic_provider_connection_attempt as attempts

    created = []
    def forbidden_session(**kwargs):
        created.append(kwargs)
        pytest.fail("unbound account attempted provider connect")
    plugin = NS(
        descriptor=_descriptor(),
        create_session=forbidden_session,
    )
    row = {
        "provider": "synthetic", "id": "credential-a",
        "enabled": True, "credentials": {"token": "private"},
    }
    host = NS(
        current_identity=NS(identity_id=selected),
        config=NS(provider_accounts=lambda enabled_only=True: [row]),
        registry=NS(all=lambda: [], maybe_get=lambda provider: plugin),
        _session_key=lambda p, c: (p, c),
        disconnect_configured=lambda: None,
        _local_account=lambda: Account(
            identity_id=selected, provider_id="local",
            credential_id="local", unique_id="local",
        ),
        identities=[NS(identity_id=selected)],
        diagnostics=NS(),
    )
    monkeypatch.setattr(
        attempts, "new_attempt",
        lambda *_a, **_k: NS(emit=lambda *_a, **_k: None),
    )
    monkeypatch.setattr(
        provider_sessions, "public_connection_failure",
        lambda *_a, **_k: {"provider_id": "synthetic", "code": "unavailable"},
    )
    monkeypatch.setattr(
        provider_sessions, "_publish_connection_failure",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        provider_sessions, "bounded_connection_failures",
        lambda failures: tuple(failures),
    )
    logger = NS(info=lambda *_a, **_k: None,
                warning=lambda *_a, **_k: None)
    result = provider_sessions.connect_configured(host, logger=logger)
    assert result["connected"] == []
    assert result["loans"] == []
    assert len(result["accounts"]) == 1
    assert result["accounts"][0].provider_id == "local"
    assert created == []
