"""P0 #3416: readable Numilog card with fully accessible scoped diagnostics.

Exercises actual GTK controller methods with headless widgets, no provider
I/O, metadata mutation, downloads or new consent.
"""
from __future__ import annotations

from copy import deepcopy

from cifereca.gui.numilog_materialization_status import describe_run
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


def test_inconsistent_auxiliary_does_not_reintroduce_false_diagnostic():
    status = _status()
    status["diagnostic"]["attempted"] = 7
    concise = describe_run("on_start", status, now=NOW, compact=True)
    detailed = describe_run("on_start", status, now=NOW)
    assert "1 téléchargé(s)" in concise
    assert "Phase :" not in detailed
    assert "tâche :" not in detailed
