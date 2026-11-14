"""#5975 / #5851: confirmed source owners never inherit GTK selection."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core import provider_sessions
from cifereca.models import Account
from cifereca.providers import ProviderConfigurationError


def _rig(configured_identity, *, navigation_identity="alice"):
    account = Account(
        identity_id="alice", provider_id="future", credential_id="card",
        unique_id="borrower",
    )
    session = NS(
        provider_id="future", credential_id="card", connected=True,
        accounts=[account], _epoch=3,
    )
    calls = []

    def bound(**kwargs):
        calls.append(kwargs)
        return True

    row = {"provider": "future", "id": "card"}
    if configured_identity is not _ABSENT:
        row["identity_id"] = configured_identity
    storage = NS(
        identity_for_connection=lambda p, c: "alice",
        provider_scope_account_bound=bound,
    )
    service = NS(
        storage=storage,
        config=NS(provider_accounts=lambda *, enabled_only: [row]),
        accounts=[], _sessions={("future", "card"): session},
        _session_key=lambda p, c: (p, c),
        identity_by_id=lambda i: NS(identity_id=i) if i == "alice" else None,
        current_identity=NS(identity_id=navigation_identity),
        _source_context_epoch=7,
    )
    return service, session, calls


_ABSENT = object()


@pytest.mark.parametrize("configured_identity", [
    _ABSENT, None, "", " ", False, 0, [], {}, "bob",
])
def test_missing_or_mismatched_config_owner_is_never_substituted_from_gtk(
    monkeypatch, configured_identity,
):
    monkeypatch.setattr(
        "cifereca.core.canonical_delivery_abi.has_complete_delivery_abi",
        lambda _session: True,
    )
    service, session, calls = _rig(configured_identity)
    with pytest.raises(
        ProviderConfigurationError, match="source_canonical_account_unavailable",
    ):
        provider_sessions.bind_confirmed_source_account(
            service, "future", "card", session,
        )
    assert calls == []
    assert service.accounts == []
    assert service._source_context_epoch == 7
    assert service.current_identity.identity_id == "alice"
    assert session.accounts[0].identity_id == "alice"


@pytest.mark.parametrize("navigation_identity", ["alice", "bob"])
def test_exact_config_and_sql_owner_allow_headless_source_binding(
    monkeypatch, navigation_identity,
):
    monkeypatch.setattr(
        "cifereca.core.canonical_delivery_abi.has_complete_delivery_abi",
        lambda _session: True,
    )
    service, session, calls = _rig("alice", navigation_identity=navigation_identity)
    assert provider_sessions.bind_confirmed_source_account(
        service, "future", "card", session,
    ) is session
    assert service.accounts == session.accounts
    assert service._source_context_epoch == 8
    assert service.current_identity.identity_id == navigation_identity
    assert calls == [{
        "identity_id": "alice",
        "provider_id": "future",
        "credential_id": "card",
        "account_id": "borrower",
    }]


def test_targeted_reconnect_refuses_missing_owner_before_disconnect():
    # Even a matching GTK selection cannot authorize an unbound configured row.
    disconnected = []
    service = NS(
        current_identity=NS(identity_id="alice"),
        config=NS(provider_accounts=lambda *, enabled_only: [
            {"id": "card", "provider": "future", "enabled": True},
        ]),
        disconnect_configured=lambda: disconnected.append(True),
    )
    with pytest.raises(ValueError, match="selected_source_not_unique"):
        provider_sessions.connect_configured(
            service,
            logger=NS(info=lambda *_args, **_kwargs: None),
            source_scope=("alice", "future", "card"),
        )
    assert disconnected == []


def test_targeted_reconnect_requires_exact_row_identity():
    rows = [
        {"id": "card", "provider": "future", "enabled": True},
        {"id": "other", "provider": "future", "identity_id": "bob"},
    ]
    with pytest.raises(ValueError, match="selected_source_not_unique"):
        provider_sessions._configured_rows_for_source(
            rows, source_scope=("alice", "future", "card"),
        )
    rows[0]["identity_id"] = "alice"
    assert provider_sessions._configured_rows_for_source(
        rows, source_scope=("alice", "future", "card"),
    ) == [rows[0]]
