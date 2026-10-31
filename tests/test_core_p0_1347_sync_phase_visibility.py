"""P0 #1347: phase-specific clocks must not masquerade as global sync success."""
from types import SimpleNamespace as NS

from cifereca.gui.sync_view import (
    sync_source_phase_summary, sync_source_presentation,
)


def _source(phases):
    return NS(
        provider_id="kobo", label="Compte Kobo",
        enabled=True, connected=True, sync_capable=True,
        status="success", last_success="2026-10-15T09:30:00+00:00",
        last_attempt="2026-10-15T09:30:00+00:00", error="",
        sync_capabilities=("remote_inventory", "entitlements", "local_materialization"),
        phase_states=tuple(phases),
    )


def phase(which, status, attempt="", success=""):
    return NS(phase=which, status=status, last_attempt=attempt, last_success=success)


def test_inventory_success_and_acquisition_partial_keep_distinct_clocks():
    item = _source((
        phase("discover_rights", "success", "2026-10-15T09:30:00Z", "2026-10-15T09:30:00Z"),
        phase("materialize", "partial", "2026-10-15T10:00:00Z", ""),
    ))
    summary = sync_source_presentation(item).summary
    assert "Notices et droits : Réussie" in summary
    assert "Téléchargement local : Partielle" in summary
    assert "dernier essai 15/10/2026 10:00" in summary
    assert summary.count("dernier succès confirmé") == 1
    assert "Téléchargement local : Partielle · dernier essai 15/10/2026 10:00 · dernier succès" not in summary


def test_interrupted_and_unsupported_acquisition_are_not_successes():
    a = _source((
        phase("discover_rights", "idle"),
        phase("materialize", "interrupted", "2026-10-15T08:00:00Z"),
    ))
    assert "Téléchargement local : Interrompue" in sync_source_phase_summary(a)
    b = _source((phase("materialize", "not_supported"),))
    assert "Téléchargement local : Non prise en charge" in sync_source_phase_summary(b)


def test_private_malformed_provider_state_never_renders():
    private = "https://provider.private/token=SECRET"
    item = _source((
        phase("discover_rights", private, private, private),
        phase("materialize", private, private, private),
        phase("reconcile", private, private, private),
    ))
    text = sync_source_phase_summary(item)
    assert "État inconnu" in text
    assert private not in text
    assert "SECRET" not in text
    assert "reconcile" not in text


def test_no_legacy_phase_metadata_remains_backward_compatible():
    item = _source(())
    assert sync_source_phase_summary(item) == ""
    summary = sync_source_presentation(item).summary
    assert "Synchronisation : " in summary
    assert "Téléchargement local :" not in summary


def test_phase_list_is_bounded_and_extra_untrusted_phases_ignored():
    rows = tuple(phase(f"PRIVATE_{n}", "failed") for n in range(9))
    assert sync_source_phase_summary(_source(rows)) == ""
    item = _source((phase("unknown_private", "success"),))
    assert sync_source_phase_summary(item) == ""



def test_unknown_materialization_capability_is_not_rewritten_by_provider_name():
    item = _source((
        phase("discover_rights", "success"),
        phase("materialize", "not_supported"),
    ))
    item.provider_id = "numilog"
    item.label = "Numilog"
    item.sync_capabilities = ("remote_inventory", "entitlements")
    summary = sync_source_presentation(item).summary
    assert "Notices et droits : Réussie" in summary
    assert "Téléchargement local : Non prise en charge" in summary
    assert "Téléchargement local : Réussie" not in summary
    # A genuine materialization failure is never replaced by this pointer.
    item.phase_states = (phase("materialize", "failed"),)
    assert "Téléchargement local : Échec" in sync_source_phase_summary(item)
