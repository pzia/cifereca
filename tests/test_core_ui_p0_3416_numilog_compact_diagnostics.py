"""P0 #3416: readable Numilog card with fully accessible scoped diagnostics.

Exercises actual GTK controller methods with headless widgets, no provider
I/O, metadata mutation, downloads or new consent.
"""
from __future__ import annotations

from copy import deepcopy

from cifereca.gui.numilog_materialization_status import describe_run
from tests.test_core_p0_1905_numilog_consent_ui import _ui
from tests.test_core_p0_2131_numilog_finalized_diagnostic_consistency import (
    NOW, _diag, _receipt,
)


def _status():
    return {
        **_receipt("complete", attempted=2, downloaded=1, failed=0),
        "diagnostic": _diag("complete", attempted=2, downloaded=1, failed=0),
    }


def test_compact_and_complete_views_share_exact_verified_result():
    data = _status()
    original = deepcopy(data)
    compact = describe_run("on_start", data, now=NOW, compact=True)
    detailed = describe_run("on_start", data, now=NOW)
    assert "Dernier essai terminé" in compact
    assert "1 téléchargé(s)" in compact
    assert "0 échec(s)" in compact
    assert "Phase :" not in compact and "tâche :" not in compact
    assert "Achats examinés" not in compact
    assert "Phase : completed" in detailed
    assert "tâche : abcdef012345" in detailed
    assert "Prochain essai :" in detailed
    assert data == original


def test_numilog_card_shows_short_state_and_collapsed_full_diagnostics():
    window, broker, calls, dialogs, state, get_row = _ui(initial="on_start")
    calls.clear()
    status = _status()
    broker.numilog_materialization_run_status = lambda sid: (
        calls.append(("read-local-status", sid)) or deepcopy(status)
    )
    window._refresh_sync_page()
    row = get_row()
    assert "Dernier essai terminé" in row.subtitle
    assert "1 téléchargé(s)" in row.subtitle
    assert "Phase :" not in row.subtitle
    assert "tâche :" not in row.subtitle
    group = window._sync_sources_box.children[0]
    disclosures = [
        node for node in group.children
        if getattr(node, "label", "") == "Détails et diagnostics · sauvegarde"
    ]
    assert len(disclosures) == 1
    details, = disclosures
    assert details.expanded is False
    assert details.child is not None
    assert "Phase : completed" in details.child.label
    assert "tâche : abcdef012345" in details.child.label
    assert "Prochain essai :" in details.child.label
    assert row.suffixes and row.suffixes[0].sensitive is True
    assert [call for call in calls if call[0] == "read-local-status"] == [
        ("read-local-status", "numilog-consent-one"),
    ]
    assert not any(call[0] == "materialization-prepare" for call in calls)
    assert window.runner.tasks == [] and dialogs == []


def test_missing_diagnostic_keeps_existing_actions_without_extra_disclosure():
    window, broker, calls, dialogs, state, get_row = _ui(initial="manual")
    broker.numilog_materialization_run_status = lambda sid: {
        "status": "never", "started_at": None, "next_eligible_at": None,
        "attempted": 0, "downloaded": 0, "skipped": 0, "failed": 0,
    }
    window._refresh_sync_page()
    assert "Aucun essai encore effectué" in get_row().subtitle
    assert not any(
        getattr(node, "label", "") == "Détails et diagnostics · sauvegarde"
        for node in window._sync_sources_box.children[0].children
    )
    assert get_row().suffixes[0].sensitive is True
    assert window.runner.tasks == [] and dialogs == []


def test_inconsistent_auxiliary_does_not_reintroduce_false_diagnostic():
    status = _status()
    status["diagnostic"]["attempted"] = 7
    concise = describe_run("on_start", status, now=NOW, compact=True)
    detailed = describe_run("on_start", status, now=NOW)
    assert "1 téléchargé(s)" in concise
    assert "Phase :" not in detailed
    assert "tâche :" not in detailed
