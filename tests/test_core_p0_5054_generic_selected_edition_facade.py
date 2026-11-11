"""The Core selected-edition facade must be provider neutral (#5054/#3196).

The underlying canonical engine owns consent, live account/session checks,
attested A, staged B, SHA and placement; the facade does not interpret rights.
"""
from types import SimpleNamespace as NS

from cifereca.services import LibraryService


def test_generic_selected_edition_facade_passes_exact_scope_and_policy(monkeypatch):
    from cifereca.core import canonical_selected_edition
    calls = []
    sentinel = {"ok": True, "schema": "core.selected-canonical/1"}
    def selected(service, **kwargs):
        calls.append((service, kwargs))
        return sentinel
    monkeypatch.setattr(canonical_selected_edition, "synchronize_selected_edition", selected)
    host = NS()
    cancelled = lambda: False
    result = LibraryService.synchronize_selected_edition(
        host, provider_id="future-provider", credential_id="credential-A",
        edition_id="edition-42", explicit_user_action=True, cancelled=cancelled,
    )
    assert result is sentinel
    assert calls == [(host, dict(
        provider_id="future-provider", credential_id="credential-A",
        edition_id="edition-42", explicit_user_action=True, cancelled=cancelled,
        result_schema="core.selected-canonical/1",
    ))]


def test_generic_selected_edition_facade_forwards_compatibility_schema(monkeypatch):
    from cifereca.core import canonical_selected_edition
    received = []
    monkeypatch.setattr(
        canonical_selected_edition, "synchronize_selected_edition",
        lambda service, **kwargs: received.append(kwargs) or {"ok": True},
    )
    LibraryService.synchronize_selected_edition(
        NS(), provider_id="kobo", credential_id="vault",
        edition_id="edition", explicit_user_action=True,
        result_schema="core.kobo.selected-canonical/1",
    )
    assert received[0]["result_schema"] == "core.kobo.selected-canonical/1"
    assert received[0]["provider_id"] == "kobo"
    assert not hasattr(LibraryService, "synchronize_kobo_selected_edition")
