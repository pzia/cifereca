"""P1 #3416: visible per-source counts without obscuring active Sync advice.

Headless execution of the real GTK controller method; no provider call,
consent change, acquisition or extra widget/suffix in the account row.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.diagnostic_source_sync import CANONICAL_EXECUTION_STATUSES
from cifereca.gui.sync_view import sync_source_attention, sync_source_cycle_pulse
from cifereca.plugin_api import SyncSourceCycleReceipt
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def _receipt(**changes):
    counts = {state: 0 for state in CANONICAL_EXECUTION_STATUSES}
    counts.update(changes)
    return SyncSourceCycleReceipt(
        "partial", "2026-11-05T04:00:00+00:00", "manual",
        tuple(counts.items()),
    )


def test_compact_projection_prioritizes_pending_and_failures_without_faking_rights():
    source = _source(last_cycle=_receipt(
        present_attested=512, downloaded=4, pending=7,
        blocked=2, failed=3, recovered=1,
    ))
    assert sync_source_cycle_pulse(source) == (
        "Dernier cycle ciblé : 7 en attente · 3 en échec · 2 bloqués"
    )
    assert "512" not in sync_source_cycle_pulse(source)
    assert sync_source_cycle_pulse(_source(
        last_cycle=_receipt(),
    )) == "Dernier cycle ciblé : 0 réception locale attestée"
    assert sync_source_cycle_pulse(_source()) == ""  # No receipt != zero.


@pytest.mark.parametrize("corruption", [
    (("pending", 7),),
    tuple((x, True if x == "failed" else 0)
          for x in CANONICAL_EXECUTION_STATUSES),
    tuple((x, 1_000_001 if x == "failed" else 0)
          for x in CANONICAL_EXECUTION_STATUSES),
    tuple((x, 0) for x in CANONICAL_EXECUTION_STATUSES)
        + (("token", "PRIVATE_SECRET"),),
    ((["unhashable"], 1),),
])
def test_malformed_or_unattested_counts_have_no_visible_pulse(corruption):
    assert sync_source_cycle_pulse(_source(last_cycle=replace(
        _receipt(), canonical_execution=corruption,
    ))) == ""
    assert "PRIVATE_SECRET" not in sync_source_cycle_pulse(_source(
        last_cycle=replace(_receipt(), canonical_execution=corruption),
    ))


def test_nonalert_source_has_compact_pulse_in_second_visible_line():
    src = _source(
        provider_id="future.provider", label="Compte fictif",
        last_cycle=_receipt(present_attested=512, pending=7, failed=3),
        status="success", last_success="2026-11-05T04:00:00+00:00",
    )
    assert sync_source_attention(src) is None
    window, _, calls, _, _ = _fixture(sources=[src])
    row = window._sync_sources_box.children[0].children[0]
    assert row.subtitle.startswith("Fournisseur : future.provider\n")
    assert "Dernier cycle ciblé : 7 en attente · 3 en échec" in row.subtitle.splitlines()[1]
    assert "présents attestés 512" in row.tooltip
    assert "en attente 7" in row.tooltip
    assert len(row.prefixes) == 0
    assert row.suffixes[1].label == "Synchroniser Compte fictif"
    assert not window.runner.tasks
    assert not any(name == "set-enabled" for name, *_ in calls)


def test_attention_first_and_next_step_kept_while_counts_show_in_line_two():
    src = _source(
        provider_id="future.provider", label="Compte fictif",
        last_cycle=_receipt(pending=7, failed=3),
        status="partial",
        phase_states=(NS(phase="fetch", status="failed"),),
    )
    alert = sync_source_attention(src)
    assert alert is not None and alert[0] == "error"
    window, _, _, _, _ = _fixture(sources=[src])
    row = window._sync_sources_box.children[0].children[0]
    assert row.subtitle.startswith(alert[1] + "\n")
    second = row.subtitle.splitlines()[1]
    assert "Dernier cycle ciblé : 7 en attente · 3 en échec" in second
    assert "examiner le dernier cycle" in second
    assert len(row.prefixes) == 1
    assert row.suffixes[1].label == "Synchroniser Compte fictif"


def test_disabled_source_keeps_historical_receipt_only_in_tooltip():
    src = _source(enabled=False, last_cycle=_receipt(pending=7, failed=3))
    window, _, _, _, _ = _fixture(sources=[src])
    row = window._sync_sources_box.children[0].children[0]
    assert row.subtitle.startswith("Synchronisation : désactivée")
    assert "Dernier cycle ciblé" not in row.subtitle
    assert "en attente 7" in row.tooltip
    assert row.suffixes[0].get_active() is False


def test_actual_partial_business_receipt_is_visible_on_its_exact_account_only():
    """Replay a documented 512/3/3/4 targeted-cycle receipt without supplier IO.

    A technical task result is not another source's business status; no
    old count may appear for an account with no exact last_cycle.
    """
    source = _source(
        identity_id="alice", source_id="kobo-alice",
        provider_id="kobo", label="Compte Alice",
        status="partial", error="Synchronisation partielle",
        last_cycle=_receipt(
            present_attested=512, pending=3, failed=3, skipped_archived=4,
        ),
    )
    alert = sync_source_attention(source)
    assert alert == ("warning", "Synchronisation : résultat partiel")
    assert sync_source_cycle_pulse(source) == (
        "Dernier cycle ciblé : 3 en attente · 3 en échec · "
        "512 présents attestés"
    )
    window, _, calls, _, _ = _fixture(sources=[source])
    row = window._sync_sources_box.children[0].children[0]
    assert row.subtitle.splitlines()[0] == alert[1]
    assert sync_source_cycle_pulse(source) in row.subtitle.splitlines()[1]
    assert "archivés ignorés 4" in row.tooltip
    assert not window.runner.tasks
    assert not any(x[0] == "set-enabled" for x in calls)

    # The second profile has no typed receipt. It must not inherit counters
    # from the same provider or the previous visible account.
    unobserved = _source(
        identity_id="bob", source_id="kobo-bob",
        provider_id="kobo", label="Compte Bob",
        status="idle", last_cycle=None,
    )
    other, _, other_calls, _, _ = _fixture(
        identity_id="bob", sources=[unobserved],
    )
    other_row = other._sync_sources_box.children[0].children[0]
    assert sync_source_cycle_pulse(unobserved) == ""
    assert "512" not in other_row.subtitle + other_row.tooltip
    assert "Dernier cycle ciblé" not in other_row.subtitle
    assert not other.runner.tasks
    assert not any(x[0] == "set-enabled" for x in other_calls)
