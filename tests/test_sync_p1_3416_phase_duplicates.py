"""#3416: duplicated Core phase observations cannot produce a false success label."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import (
    sync_source_attention,
    sync_source_phase_summary,
    sync_source_presentation,
)


def source(*phases):
    return NS(
        provider_id="provider.future", label="Compte fictif",
        enabled=True, connected=True, sync_capable=True,
        status="partial", error="",
        last_attempt="", last_success="",
        sync_capabilities=("remote_inventory", "local_materialization"),
        phase_states=phases,
    )


def phase(which, status, attempt="", success=""):
    return NS(phase=which, status=status, last_attempt=attempt, last_success=success)


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_local_phase_is_never_displayed_as_first_success(reverse):
    rows = [
        phase("materialize", "success", "2026-10-30T15:00:00Z",
              "2026-10-30T15:00:00Z"),
        phase("materialize", "failed", "2026-10-30T16:00:00Z"),
    ]
    item = source(*(tuple(reversed(rows)) if reverse else tuple(rows)))
    summary = sync_source_phase_summary(item)
    assert summary == "Téléchargement local : État incohérent (plusieurs résultats)"
    assert "Réussie" not in summary
    assert "dernier succès" not in summary
    assert sync_source_attention(item) == (
        "error", "Téléchargement local : échec — action nécessaire",
    )
    assert summary in sync_source_presentation(item).summary


def test_duplicate_inventory_phase_does_not_hide_independent_local_result():
    item = source(
        phase("discover_rights", "success", success="2026-10-30T12:00:00Z"),
        phase("discover_rights", "partial", attempt="2026-10-30T13:00:00Z"),
        phase("materialize", "success", success="2026-10-30T14:00:00Z"),
    )
    summary = sync_source_phase_summary(item)
    assert "Notices et droits : État incohérent (plusieurs résultats)" in summary
    assert "Notices et droits : Réussie" not in summary
    assert "Téléchargement local : Réussie" in summary
    assert "dernier succès confirmé" in summary
    assert sync_source_attention(item) == (
        "warning", "Notices et droits : résultat partiel",
    )


def test_single_legitimate_phase_retains_its_confirmed_clock():
    item = source(
        phase("discover_rights", "success", success="2026-10-30T12:00:00Z"),
        phase("materialize", "success", success="2026-10-30T14:00:00Z"),
    )
    summary = sync_source_phase_summary(item)
    assert summary.count("Réussie") == 2
    assert "État incohérent" not in summary


def test_untrusted_unknown_phase_cannot_poison_known_phase_summary():
    item = source(
        phase("private-not-a-phase", "failed", "SECRET"),
        phase("discover_rights", "success"),
    )
    summary = sync_source_phase_summary(item)
    assert summary == "Notices et droits : Réussie"
    assert "SECRET" not in summary
