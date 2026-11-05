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
        "Inventaire distant : réussie · Offres de livraison : réussie (étape seulement)",
    ),
    (
        (phase("discover_rights", "success"), phase("offer", "failed")),
        "Inventaire distant : réussie · Offres de livraison : échec",
    ),
    (
        (phase("offer", "partial"), phase("fetch", "success")),
        "Offres de livraison : partielle · Livraison locale : réussie (étape seulement)",
    ),
    (
        (phase("offer", "success"), phase("offer", "failed"),
         phase("placement", "success")),
        "Offres de livraison : non confirmée · Livraison locale : réussie",
    ),
    (
        (phase("offer", "PRIVATE_OFFER_SECRET"), phase("fetch", "failed")),
        "Offres de livraison : non confirmée · Livraison locale : échec",
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


def test_offer_failure_never_becomes_a_download_or_source_success():
    source = _source(
        provider_id="future-provider", status="partial",
        error="Synchronisation partielle",
        phase_states=(
            phase("discover_rights", "success"),
            phase("offer", "failed"),
            phase("placement", "success"),
        ),
    )
    expected = (
        "Inventaire distant : réussie · Offres de livraison : échec "
        "· Livraison locale : réussie"
    )
    assert sync_source_cycle_headline(source) == expected
    alert = sync_source_attention(source)
    assert alert == ("error", "Offre de livraison : échec — action nécessaire")
    view = sync_source_presentation(source)
    assert view.sync_label == "Échec"
    assert expected in view.summary
    assert "fichiers reçus" not in view.summary.casefold()


def test_offer_stage_missing_or_idle_is_not_invented_as_absent():
    no_offer = _source(
        provider_id="future-provider",
        phase_states=(phase("discover_rights", "success"),),
    )
    assert sync_source_cycle_headline(no_offer) == "Inventaire distant : réussie"
    idle = _source(
        provider_id="future-provider",
        phase_states=(phase("offer", "idle"),),
    )
    assert sync_source_cycle_headline(idle) == (
        "Offres de livraison : non exécutée"
    )
    for source in (no_offer, idle):
        text = sync_source_presentation(source).summary
        assert "aucune offre" not in text.casefold()
        assert "offre sélectionnée" not in text.casefold()


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


@pytest.mark.parametrize(("stop_code", "visible"), [
    ("source_changed", "source modifiée"),
    ("source_disabled", "source désactivée"),
    ("source_disconnected", "source déconnectée"),
])
def test_core_bounded_stop_reason_is_visible_without_losing_real_failure(stop_code, visible):
    """These are already published Core phase codes, not supplier strings."""
    source = _source(
        provider_id="fictional", status="partial",
        phase_states=(phase("discover_rights", "success"),
                      phase("materialize", stop_code)),
    )
    headline = sync_source_cycle_headline(source)
    assert headline == f"Inventaire distant : réussie · Livraison locale : {visible}"
    assert headline in sync_source_presentation(source).summary

    with_actual_failure = _source(
        provider_id="fictional", phase_states=(
            phase("materialize", stop_code), phase("fetch", "failed"),
        ),
    )
    assert sync_source_cycle_headline(with_actual_failure) == "Livraison locale : échec"

    # A free-text payload, even one resembling a source code, is not a state.
    hostile = _source(phase_states=(phase("materialize", "private://" + stop_code),))
    assert sync_source_cycle_headline(hostile) == "Livraison locale : non confirmée"


def test_local_busy_phase_is_visible_in_both_sync_headline_and_attention():
    """Core's busy is contention, never a successful local placement."""
    source = _source(
        provider_id="fictional", status="running",
        phase_states=(
            phase("discover_rights", "success"),
            phase("fetch", "busy"),
            phase("materialize", "running"),
        ),
    )
    expected = "Inventaire distant : réussie · Livraison locale : déjà en cours"
    assert sync_source_cycle_headline(source) == expected
    assert expected in sync_source_presentation(source).summary
    assert sync_source_attention(source) == (
        "warning", "Réception du fichier : déjà en cours",
    )


def test_real_failure_remains_above_busy_local_phase():
    source = _source(
        provider_id="fictional", status="partial",
        phase_states=(
            phase("fetch", "failed"),
            phase("placement", "busy"),
        ),
    )
    assert sync_source_cycle_headline(source) == "Livraison locale : échec"
    assert sync_source_attention(source) == (
        "error", "Réception du fichier : échec — action nécessaire",
    )


@pytest.mark.parametrize(("stages", "expected"), [
    (
        (phase("materialize", "success"), phase("placement", "not_supported")),
        "Livraison locale : réussie (étape seulement)",
    ),
    (
        (phase("fetch", "success"), phase("placement", "not_supported")),
        "Livraison locale : réussie (étape seulement)",
    ),
    (
        (phase("fetch", "success"), phase("placement", "success")),
        "Livraison locale : réussie",
    ),
    (
        (phase("materialize", "success"), phase("placement", "success")),
        "Livraison locale : réussie",
    ),
    (
        (phase("fetch", "failed"), phase("placement", "not_supported")),
        "Livraison locale : échec",
    ),
])
def test_placement_absent_or_unsupported_never_certifies_local_delivery(
    stages, expected,
):
    """An upstream successful stage cannot stand in for a placement receipt."""
    source = _source(
        provider_id="fictional",
        phase_states=(phase("discover_rights", "success"), *stages),
    )
    headline = sync_source_cycle_headline(source)
    assert headline == "Inventaire distant : réussie · " + expected
    assert headline in sync_source_presentation(source).summary


def test_duplicate_placement_still_prevents_green_last_run_headline():
    source = _source(
        provider_id="fictional",
        phase_states=(
            phase("fetch", "success"),
            phase("placement", "success"),
            phase("placement", "not_supported"),
        ),
    )
    assert sync_source_cycle_headline(source) == "Livraison locale : non confirmée"
