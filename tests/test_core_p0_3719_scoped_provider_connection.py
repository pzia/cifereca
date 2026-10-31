"""#3719: connection targeting cannot authenticate another configured credential."""
from types import SimpleNamespace

import pytest

from cifereca.core import provider_sessions
from cifereca.services import LibraryService


ROWS = [
    {"identity_id": "A", "provider": "numilog", "id": "credential-one"},
    {"identity_id": "A", "provider": "kobo", "id": "credential-two"},
    {"identity_id": "B", "provider": "numilog", "id": "credential-three"},
]


def test_exact_configured_credential_only_and_gui_unfiltered():
    assert provider_sessions._configured_rows_for_source(
        ROWS, source_scope=None, default_identity="A",
    ) is ROWS
    assert provider_sessions._configured_rows_for_source(
        ROWS, source_scope=("A", "numilog", "credential-one"),
        default_identity="A",
    ) == ROWS[:1]
    assert provider_sessions._configured_rows_for_source(
        ROWS, source_scope=("B", "numilog", "credential-three"),
        default_identity="A",
    ) == ROWS[2:]


@pytest.mark.parametrize("scope", [
    ("A", "numilog", "missing"),
    ("B", "numilog", "credential-one"),
    ("A", "unknown", "credential-one"),
])
def test_scope_mismatch_refuses_connection(scope):
    with pytest.raises(ValueError, match="selected_source_not_unique"):
        provider_sessions._configured_rows_for_source(
            ROWS, source_scope=scope, default_identity="A",
        )


@pytest.mark.parametrize("scope", [
    ("A", "numilog"),
    ("A", "numilog", "credential-one\nsecret"),
    ("A", "numilog", "credential-one\x00secret"),
    ("", "numilog", "credential-one"),
    ["A", "numilog", "credential-one"],
])
def test_malformed_scope_refused_before_provider_creation(scope):
    with pytest.raises(ValueError, match="selected_source_invalid_scope"):
        provider_sessions._configured_rows_for_source(
            ROWS, source_scope=scope, default_identity="A",
        )


def test_public_facade_forwards_explicit_scope_without_global_connection(monkeypatch):
    calls = []
    def stub(service, *, logger, source_scope=None, **kwargs):
        calls.append((service, source_scope))
        return {"accounts": []}
    monkeypatch.setattr(provider_sessions, "connect_configured", stub)
    sentinel = SimpleNamespace()
    result = LibraryService.connect_selected_source(
        sentinel, identity="A", provider="numilog", credential="credential-one",
    )
    assert result == {"accounts": []}
    assert calls == [(sentinel, ("A", "numilog", "credential-one"))]


def test_rejected_source_does_not_disconnect_live_sessions():
    calls = []
    service = SimpleNamespace(
        config=SimpleNamespace(
            provider_accounts=lambda *, enabled_only: ROWS,
        ),
        current_identity=SimpleNamespace(identity_id="A"),
        disconnect_configured=lambda: calls.append("disconnected"),
    )
    logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="selected_source_not_unique"):
        provider_sessions.connect_configured(
            service, logger=logger,
            source_scope=("A", "numilog", "wrong-credential"),
        )
    assert calls == []
