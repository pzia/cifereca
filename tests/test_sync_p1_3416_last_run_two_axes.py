"""#3416: last-run remote inventory and local delivery are separate states.

Presentation-only contract: no provider dispatch, arbitrary provider text,
borrow/return/ACSM network, snapshots or consent grants.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import (
    sync_source_attention, sync_source_cycle_headline, sync_source_presentation,
)
from tests.test_core_ui_1347_sync_action_scope import _source, _fixture


def phase(name, status):
    return NS(phase=name, status=status)


@pytest.mark.parametrize(("phases", "expected"), [
    (
        (phase("discover_rights", "success"), phase("materialize", "failed")),
        "Inventaire distant : réussie · Livraison locale : échec",
    ),
    (
        (phase("discover_rights", "partial"), phase("materialize", "success")),
        "Inventaire distant : partielle · Livraison locale : réussie (étape seulement)",
    ),
    (
        (phase("discover_rights", "success"), phase("materialize", "not_supported"),
         phase("fetch", "failed"), phase("placement", "success")),
        "Inventaire distant : réussie · Livraison locale : échec",
    ),
    (
        (phase("discover_rights", "success"), phase("fetch", "success"),
         phase("placement", "success")),
        "Inventaire distant : réussie · Livraison locale : réussie",
    ),
    (
        (phase("discover_rights", "partial"), phase("materialize", "not_supported"),
         phase("reconcile", "not_supported")),
        "Inventaire distant : partielle · Livraison locale : non disponible",
    ),
    (
        (phase("discover_rights", "failed"),),
        "Inventaire distant : échec",
    ),
    (
        (phase("materialize", "success"),),
        "Livraison locale : réussie (étape seulement)",
    ),
    (
        (phase("discover_rights", "success"), phase("offer", "success")),
        "Inventaire distant : réussie",
    ),
    (
        (phase("discover_rights", "success"), phase("discover_rights", "failed"),
         phase("placement", "success")),
        "Inventaire distant : non confirmée · Livraison locale : réussie",
    ),
    (
        (phase("discover_rights", "PRIVATE_URL"), phase("fetch", "SECRET")),
        "Inventaire distant : non confirmée · Livraison locale : non confirmée",
    ),
])
def test_two_axis_bounded_headline_retains_actual_phase_semantics(phases, expected):
    source = _source(provider_id="unknown-provider", phase_states=phases)
    assert sync_source_cycle_headline(source) == expected
    assert expected in sync_source_presentation(source).summary
    assert "SECRET" not in sync_source_presentation(source).summary
    assert "PRIVATE_URL" not in sync_source_presentation(source).summary


def test_outcome_remains_actionable_when_remote_inventory_completed_but_file_failed():
    source = _source(
        provider_id="paris", label="Compte", status="partial",
        error="Synchronisation partielle", last_success="2026-10-20T10:00:00Z",
        phase_states=(phase("discover_rights", "success"),
                      phase("fetch", "failed"), phase("placement", "partial")),
    )
    assert sync_source_attention(source) == (
        "error", "Réception du fichier : échec — action nécessaire",
    )
    card = sync_source_presentation(source)
    assert card.sync_label == "Échec"
    assert "Inventaire distant : réussie · Livraison locale : échec" in card.summary
    assert "Dernier succès confirmé" in card.summary


def test_incomplete_or_untyped_phases_never_mint_current_success_or_local_presence():
    source = _source(status="partial", phase_states=(
        phase("discover_rights", "partial"),
        phase("materialize", "not_supported"),
        phase("reconcile", "not_supported"),
    ))
    presentation = sync_source_presentation(source)
    assert "Inventaire distant : partielle" in presentation.summary
    assert "Livraison locale : non disponible" in presentation.summary
    assert "fichiers vérifiés" not in presentation.summary.casefold()
    assert "4/4" not in presentation.summary

    for invalid in ("secret", {"phase": "fetch"}, tuple(phase("fetch", "success") for _ in range(9))):
        assert sync_source_cycle_headline(_source(phase_states=invalid)) == ""


def test_visible_sync_row_shows_two_axes_without_new_runner_or_provider_io():
    source = _source(
        provider_id="fictional", label="Fournisseur fictif", status="partial",
        error="Synchronisation partielle", phase_states=(
            phase("discover_rights", "success"), phase("materialize", "failed"),
        ),
    )
    window, _, calls, _, _ = _fixture(sources=[source])
    account_row = window._sync_sources_box.children[0].children[0]
    assert "Inventaire distant : réussie" in account_row.subtitle
    assert "Livraison locale : échec" in account_row.subtitle
    assert not window.runner.tasks
    assert all(call[0] == "sources" for call in calls)
