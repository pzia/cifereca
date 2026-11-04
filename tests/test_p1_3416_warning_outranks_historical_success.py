"""P1 #3416: a phase warning must not be hidden by a historical success.

Pure CoreUI projection and synthetic GTK card. No provider I/O, TaskRunner
enqueue, download, consent write or source mutation is triggered by rendering.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import sync_source_attention, sync_source_presentation
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


OLD_SUCCESS = "2026-10-31T09:00:00"
LAST_ATTEMPT = "2026-11-04T20:00:00"


def phase(name, status):
    return NS(phase=name, status=status, last_attempt="", last_success="")


@pytest.mark.parametrize("provider_id", ["fictional", "kobo", "paris"])
@pytest.mark.parametrize("status", ["success", "idle"])
@pytest.mark.parametrize("phases", [
    (phase("offer", "partial"),),
    (phase("discover_rights", "partial"),),
    (phase("materialize", "busy"),),
    (phase("offer", "success"), phase("offer", "success")),
    (phase("placement", "attention_required"),),
])
def test_nonfatal_phase_warning_outranks_a_historical_success(
    provider_id, status, phases,
):
    src = _source(
        provider_id=provider_id, status=status, error="",
        last_success=OLD_SUCCESS, last_attempt=LAST_ATTEMPT,
        phase_states=phases,
    )
    assert sync_source_attention(src)[0] == "warning"
    shown = sync_source_presentation(src)
    assert shown.sync_label == "État à vérifier"
    assert "Synchronisation : État à vérifier" in shown.summary
    assert "Synchronisation : Dernier succès" not in shown.summary
    assert "Dernier succès confirmé : 31/10/2026 09:00" in shown.summary
    assert shown.action_enabled is True


def test_disconnection_alert_is_not_erased_by_old_success():
    src = _source(
        provider_id="provider.future", status="success", error="",
        connected=False, last_success=OLD_SUCCESS,
    )
    assert sync_source_attention(src) == (
        "warning", "Compte déconnecté — reconnexion nécessaire"
    )
    shown = sync_source_presentation(src)
    assert shown.sync_label == "État à vérifier"
    assert shown.connection_label == "Déconnectée"
    assert shown.action_enabled is False
    assert "Dernier succès confirmé : 31/10/2026 09:00" in shown.summary


@pytest.mark.parametrize("state,expected", [
    ("running", "En cours…"),
    ("partial", "Partielle"),
    ("blocked", "Bloquée"),
    ("cancelled", "Annulée"),
    ("interrupted", "Interrompue"),
])
def test_source_current_state_remains_primary_over_nonfatal_stage_warning(
    state, expected,
):
    src = _source(
        provider_id="future", status=state, error="",
        last_success=OLD_SUCCESS, phase_states=(phase("offer", "partial"),),
    )
    shown = sync_source_presentation(src)
    assert shown.sync_label == expected
    assert "Dernier succès confirmé : 31/10/2026 09:00" in shown.summary


def test_explicitly_disabled_source_does_not_inherit_old_phase_warning():
    src = _source(
        enabled=False, status="success", last_success=OLD_SUCCESS,
        phase_states=(phase("offer", "partial"),),
    )
    assert sync_source_attention(src) is None
    shown = sync_source_presentation(src)
    assert shown.sync_label == "Désactivée · aucune synchronisation attendue"
    assert "Historique du dernier cycle avant désactivation :" in shown.summary


def test_clean_success_stays_last_success_and_no_fake_urgent_action():
    src = _source(
        provider_id="future", status="success", error="",
        last_success=OLD_SUCCESS, phase_states=(phase("offer", "success"),),
    )
    assert sync_source_attention(src) is None
    shown = sync_source_presentation(src)
    assert shown.sync_label == "Dernier succès 31/10/2026 09:00"
    assert "Dernier succès confirmé :" not in shown.summary


def test_severe_error_still_beats_nonfatal_warning_and_historical_success():
    src = _source(
        status="success", error="", last_success=OLD_SUCCESS,
        phase_states=(phase("offer", "partial"), phase("fetch", "failed")),
    )
    assert sync_source_attention(src) == (
        "error", "Réception du fichier : échec — action nécessaire",
    )
    assert sync_source_presentation(src).sync_label == "Échec"


def test_actual_sync_card_renders_nonfatal_warning_without_starting_any_job():
    src = _source(
        provider_id="future", label="Compte test", status="success",
        error="", last_success=OLD_SUCCESS, last_attempt=LAST_ATTEMPT,
        phase_states=(phase("offer", "partial"),),
    )
    window, broker, calls, _, _ = _fixture(sources=[src])
    row = window._sync_sources_box.children[0].children[0]
    assert row.subtitle.startswith("Offre de livraison : résultat partiel")
    assert "Synchronisation : État à vérifier" in row.tooltip
    assert "Dernier succès confirmé : 31/10/2026 09:00" in row.tooltip
    assert not window.runner.tasks
    assert calls == [("sources", "alice")]
