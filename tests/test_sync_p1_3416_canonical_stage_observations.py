"""#3416 — provider-neutral, evidence-only A/B/C Sync phase presentation.

No fabricated publication, GET or successful reconciliation when the Core
does not report one. No provider, credential, network or file mutation.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import (
    sync_source_attention,
    sync_source_phase_summary,
    sync_source_presentation,
)
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def observed(phase, status, *, attempt="", success="", extra=""):
    return NS(
        phase=phase, status=status,
        last_attempt=attempt, last_success=success,
        error=extra,
    )


def source(*rows, status="success", error=""):
    return _source(
        provider_id="vendor.future-v2",
        source_id="future-source",
        label="Source indépendante",
        status=status, error=error,
        sync_capabilities=("remote_inventory", "local_materialization"),
        phase_states=tuple(rows),
    )


def test_attested_stages_are_independent_with_distinct_success_clocks():
    rows = (
        observed("discover_rights", "success",
                 success="2026-10-31T09:00:00Z"),
        observed("offer", "success", success="2026-10-31T09:01:00Z"),
        observed("fetch", "failed", attempt="2026-10-31T09:02:00Z"),
        observed("placement", "idle"),
        observed("reconcile", "not_supported"),
    )
    value = source(*rows)
    summary = sync_source_phase_summary(value)
    assert "Notices et droits : Réussie" in summary
    assert "Offre de livraison : Réussie" in summary
    assert "Réception du fichier : Échec" in summary
    assert "Placement en bibliothèque : Jamais exécutée" in summary
    assert "Rapprochement des fichiers : Non prise en charge" in summary
    assert "Placement en bibliothèque : Réussie" not in summary
    assert summary.count("dernier succès confirmé") == 2
    assert sync_source_attention(value) == (
        "error", "Réception du fichier : échec — action nécessaire",
    )
    presented = sync_source_presentation(value)
    assert presented.sync_label == "Échec"
    assert "Réception du fichier : Échec" in presented.summary


@pytest.mark.parametrize(("phase", "heading"), [
    ("offer", "Offre de livraison"),
    ("fetch", "Réception du fichier"),
    ("placement", "Placement en bibliothèque"),
])
def test_real_failure_in_new_phase_is_actionable_even_if_source_status_is_success(
    phase, heading,
):
    value = source(observed("discover_rights", "success"),
                   observed(phase, "failed"))
    assert sync_source_attention(value) == (
        "error", f"{heading} : échec — action nécessaire",
    )
    assert sync_source_presentation(value).sync_label == "Échec"


def test_reconciliation_failure_never_retroactively_invalidates_placed_file():
    value = source(
        observed("discover_rights", "success"),
        observed("placement", "success"),
        observed("reconcile", "failed", extra="Bearer PRIVATE_TOKEN"),
    )
    assert sync_source_attention(value) == (
        "warning", "Rapprochement des fichiers : maintenance à vérifier",
    )
    presented = sync_source_presentation(value)
    assert presented.sync_label != "Échec"
    assert "Placement en bibliothèque : Réussie" in presented.summary
    assert "Rapprochement des fichiers : Échec" in presented.summary
    assert "PRIVATE_TOKEN" not in presented.summary


def test_placement_failure_beats_reconciliation_and_inventory_warnings():
    value = source(
        observed("discover_rights", "partial"),
        observed("reconcile", "failed"),
        observed("placement", "blocked"),
        status="partial", error="Synchronisation partielle",
    )
    assert sync_source_attention(value) == (
        "error", "Placement en bibliothèque : bloqué — action nécessaire",
    )
    assert sync_source_presentation(value).sync_label == "Échec"


def test_extra_core_stages_not_in_dto_are_not_invented():
    value = source(observed("discover_rights", "success"))
    summary = sync_source_phase_summary(value)
    assert summary == "Notices et droits : Réussie"
    assert "Réception du fichier" not in summary
    assert "Placement en bibliothèque" not in summary
    assert "Rapprochement des fichiers" not in summary
    assert sync_source_attention(value) is None


def test_unknown_phase_and_private_supplier_text_never_render():
    private = "https://PRIVATE.example.invalid/?token=SECRET"
    value = source(
        observed("supplied-" + private, "failed", extra=private),
        observed("offer", private, extra=private),
        observed("placement", "success", extra=private),
    )
    summary = sync_source_phase_summary(value)
    assert summary == (
        "Offre de livraison : État inconnu\n"
        "Placement en bibliothèque : Réussie"
    )
    assert private not in summary
    assert sync_source_attention(value) is None


@pytest.mark.parametrize("reverse", [True, False])
def test_duplicate_placement_observations_are_never_chosen_by_order(reverse):
    entries = [
        observed("placement", "success",
                 success="2026-10-31T14:00:00Z"),
        observed("placement", "failed",
                 attempt="2026-10-31T15:00:00Z"),
    ]
    value = source(*(reversed(entries) if reverse else entries))
    assert sync_source_phase_summary(value) == (
        "Placement en bibliothèque : État incohérent (plusieurs résultats)"
    )
    assert sync_source_attention(value) == (
        "error", "Placement en bibliothèque : échec — action nécessaire",
    )


def test_real_gtk_source_row_displays_attested_placement_failure_only():
    value = source(
        observed("discover_rights", "success"),
        observed("placement", "failed"),
    )
    window, _broker, calls, _build, _page = _fixture(sources=[value])
    row = window._sync_sources_box.children[0].children[0]
    assert row.subtitle.startswith(
        "Placement en bibliothèque : échec — action nécessaire\n"
    )
    assert "Placement en bibliothèque : Échec" in row.subtitle
    assert row.prefixes[0].icon_name == "dialog-error-symbolic"
    assert window.runner.tasks == []
    assert calls == [("sources", "alice")]
