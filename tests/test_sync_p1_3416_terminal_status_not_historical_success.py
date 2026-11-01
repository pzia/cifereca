"""P1 #3416: current source state must outrank a historical successful run.

Headless CoreUI projection and fake GTK renderer; no provider, network,
consent mutation, download or operation enqueued by rendering.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import sync_source_attention, sync_source_presentation
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source

PAST_SUCCESS = "2026-10-31T09:00:00"
RECENT_ATTEMPT = "2026-11-01T12:30:00"


@pytest.mark.parametrize(("status", "expected"), [
    ("blocked", "Bloquée"),
    ("attention_required", "Action nécessaire"),
    ("cancelled", "Annulée"),
    ("interrupted", "Interrompue"),
    ("unknown", "État non confirmé"),
    ("unrecognized_future_state", "État non confirmé"),
])
@pytest.mark.parametrize("provider_id", ["fictif.future", "kobo", "paris"])
def test_terminal_state_precedes_historical_success(status, expected, provider_id):
    src = _source(
        provider_id=provider_id, status=status, error="",
        last_success=PAST_SUCCESS, last_attempt=RECENT_ATTEMPT,
    )
    shown = sync_source_presentation(src)
    assert shown.sync_label == expected
    assert "Synchronisation : " + expected in shown.summary
    assert "Dernier succès confirmé : 31/10/2026 09:00" in shown.summary
    assert "Synchronisation : Dernier succès" not in shown.summary
    assert shown.action_enabled is True  # Read-only projection, no new policy.


def test_historical_success_remains_visible_for_idle_and_success_sources():
    for status in ("idle", "success"):
        shown = sync_source_presentation(_source(
            provider_id="future", status=status, last_success=PAST_SUCCESS,
        ))
        assert shown.sync_label == "Dernier succès 31/10/2026 09:00"
        assert "Dernier succès confirmé :" not in shown.summary


@pytest.mark.parametrize("bad", [None, False, 47, {}, ["success"], "x" * 300])
def test_untrusted_status_never_borrows_historical_success(bad):
    shown = sync_source_presentation(_source(
        provider_id="future", status=bad, last_success=PAST_SUCCESS,
    ))
    assert shown.sync_label == "État non confirmé"
    assert "Dernier succès confirmé : 31/10/2026 09:00" in shown.summary
    assert "Synchronisation : Dernier succès" not in shown.summary


def test_failed_phase_still_beats_cancelled_source_status():
    src = _source(
        provider_id="future", status="cancelled",
        last_success=PAST_SUCCESS,
        phase_states=(NS(phase="fetch", status="failed"),),
    )
    assert sync_source_attention(src)[0] == "error"
    assert sync_source_presentation(src).sync_label == "Échec"


@pytest.mark.parametrize(("status", "expected"), [
    ("blocked", "Bloquée"),
    ("cancelled", "Annulée"),
    ("interrupted", "Interrompue"),
])
def test_gtk_card_uses_terminal_status_without_running_any_sync(status, expected):
    src = _source(provider_id="new.provider", label="Source fictive",
                  source_id="source-synthetic", status=status,
                  last_success=PAST_SUCCESS, last_attempt=RECENT_ATTEMPT)
    window, broker, calls, _build, _page = _fixture(sources=[src])
    row = window._sync_sources_box.children[0].children[0]
    assert "Synchronisation : " + expected in row.subtitle
    assert "Dernier succès confirmé" in row.subtitle
    assert not window.runner.tasks
    assert not any(name == "set-enabled" for name, *args in calls)
