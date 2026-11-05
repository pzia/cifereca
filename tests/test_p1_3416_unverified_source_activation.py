"""#3416: Sync must distinguish explicit OFF from an unverified activation flag.

Presentation-only tests, no provider IO, no source mutation, no new permission.
An unknown state cannot inherit a past success as a current green headline.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import sync_source_attention, sync_source_presentation


def _source(enabled, *, status="success", phase_states=(), error=""):
    return NS(
        provider_id="future-provider", label="Source fictive",
        enabled=enabled, connected=True, sync_capable=True,
        status=status, error=error, phase_states=phase_states,
        last_success="2026-11-01T12:00:00",
        last_attempt="2026-11-02T12:00:00",
        sync_capabilities=("remote_inventory",),
    )


@pytest.mark.parametrize("bad", [None, "false", "true", 0, 1, [], {}])
def test_unverified_activation_is_neither_off_nor_historical_success(bad):
    view = sync_source_presentation(_source(bad))
    assert view.activation_label == "Non confirmée"
    assert view.sync_label == "Activation non confirmée"
    assert view.action_enabled is False
    assert "Dernier succès confirmé : 01/11/2026 12:00" in view.summary
    assert "Synchronisation : Dernier succès" not in view.summary
    assert "Activation : Désactivée" not in view.summary
    assert "actualisez la source" in view.disabled_reason


def test_explicit_off_preserves_its_existing_history_message():
    view = sync_source_presentation(_source(False))
    assert view.activation_label == "Désactivée"
    assert view.sync_label == "Désactivée · aucune synchronisation attendue"
    assert view.action_enabled is False
    assert "Historique du dernier cycle avant désactivation" not in view.summary


def test_explicit_on_preserves_normal_current_success():
    view = sync_source_presentation(_source(True))
    assert view.activation_label == "Activée"
    assert view.sync_label == "Dernier succès 01/11/2026 12:00"
    assert view.action_enabled is True


def test_real_failure_is_not_hidden_by_unknown_activation():
    failed = _source(
        None, status="partial", error="Synchronisation partielle",
        phase_states=(NS(phase="fetch", status="failed"),),
    )
    assert sync_source_attention(failed) == (
        "error", "Réception du fichier : échec — action nécessaire",
    )
    view = sync_source_presentation(failed)
    assert view.activation_label == "Non confirmée"
    assert view.sync_label == "Échec"
    assert view.action_enabled is False
    assert "Dernier succès confirmé" in view.summary


def test_no_provider_specific_policy_in_unknown_activation_projection():
    for supplier in ("paris", "kobo", "new-provider"):
        src = _source(None)
        src.provider_id = supplier
        view = sync_source_presentation(src)
        assert view.sync_label == "Activation non confirmée"
        assert not view.action_enabled
