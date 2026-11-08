"""#5078: an older local checkpoint is history, not a current sync failure.

The current canonical receipt and legacy materialization clock belong to the
same source object. This test performs no provider I/O or GTK interaction.
"""
from dataclasses import replace

import pytest

from cifereca.core.diagnostic_source_sync import CANONICAL_EXECUTION_STATUSES
from cifereca.gui.sync_view import (
    sync_source_attention, sync_source_phase_summary, sync_source_presentation,
)
from cifereca.plugin_api import SyncPhaseState, SyncSourceCycleReceipt, SyncSourceRef


def _source(
    *, local_status="partial",
    last_attempt="2026-10-30T09:58:30+00:00",
    cycle_status="success",
    cycle_at="2026-11-08T10:36:54+00:00",
    counts=None,
    status="ok",
):
    values = {state: 0 for state in CANONICAL_EXECUTION_STATUSES}
    values["present_attested"] = 517
    values["skipped_archived"] = 5
    if counts:
        values.update(counts)
    return SyncSourceRef(
        source_id="fixture:source", identity_id="alice",
        provider_id="fixture", account_id="synthetic", label="Synthetic",
        enabled=True, connected=True, sync_capable=True,
        status=status, last_attempt=cycle_at, last_success=cycle_at,
        sync_capabilities=("local_materialization",),
        phase_states=(
            SyncPhaseState("discover_rights", "success"),
            SyncPhaseState("materialize", local_status, last_attempt=last_attempt),
            SyncPhaseState("reconcile", "not_supported"),
        ),
        last_cycle=SyncSourceCycleReceipt(
            status=cycle_status, attempted_at=cycle_at, mode="manual",
            canonical_execution=tuple((key, values[key]) for key in CANONICAL_EXECUTION_STATUSES),
        ),
    )


def test_old_checkpoint_remains_visible_as_history_not_active_warning():
    source = _source()
    assert sync_source_attention(source) is None
    summary = sync_source_phase_summary(source)
    assert "Partielle (historique, antérieur au dernier cycle canonique)" in summary
    assert "30/10/2026" in summary
    assert "Dernier succès" in sync_source_presentation(source).sync_label


@pytest.mark.parametrize("changes", [
    {"cycle_status": "partial"},
    {"counts": {"failed": 1}},
    {"counts": {"pending": 1}},
    {"counts": {"blocked": 1}},
    {"last_attempt": "2026-11-08T11:36:55+00:00"},
    {"last_attempt": ""},
    {"status": "partial"},
])
def test_unqualified_current_or_unknown_local_failure_keeps_warning(changes):
    source = _source(**changes)
    assert sync_source_attention(source) == (
        "warning", "Téléchargement local : résultat partiel"
    )
    assert "historique, antérieur" not in sync_source_phase_summary(source)


def test_newer_real_failed_local_phase_is_not_masked_by_old_success():
    source = _source(
        local_status="failed",
        last_attempt="2026-11-08T11:38:00+00:00",
    )
    assert sync_source_attention(source) == (
        "error", "Téléchargement local : échec — action nécessaire"
    )


def test_other_source_cannot_borrow_unrelated_cycle_evidence():
    old = _source()
    no_receipt = replace(old, source_id="fixture:another-source", last_cycle=None)
    assert sync_source_attention(no_receipt) == (
        "warning", "Téléchargement local : résultat partiel"
    )
