"""P0 #2730: reconciliation explanations in Diagnostics are aggregate-only."""
from __future__ import annotations

import json

import pytest

from diagnostics_explorer import sanitized_rows, export_selected_events
from diagnostics_reconciliation import reconciliation_event_summary

PRIVATE = "Private Book Title /home/secret/user 9780306406157"


def event(*, status="partial", reasons=None, counts=None, phase_ms=None):
    reasons = ["time_budget", "epub_parser_limit_or_invalid"] if reasons is None else reasons
    return {
        "source": "core_events", "module": "LIBRARY",
        "timestamp": "2026-10-21T19:34:12.000+00:00",
        "event_id": "reconcile-synthetic", "severity": "warning",
        "message": "réconciliation locale incomplète",
        "status": status,
        "detail": {
            "schema_version": 1, "operation": "library_reconciliation_audit",
            "phase": "finished", "status": status,
            "code": sorted(reasons)[0] if reasons else "none",
            "reasons": reasons, "duration_ms": 134325,
            "counts": counts if counts is not None else {
                "files_started": 321, "files_observed": 320,
                "files_hashed": 320, "catalog_rows_examined": 522,
                "references_examined": 740, "candidate_files_examined": 320,
                "metadata_candidates": 0, "private_book_title": PRIVATE,
            },
            "phase_ms": phase_ms if phase_ms is not None else {
                "sha256": 8100, "epub_opf": 1100, "structure": 3100,
                "candidate_index": 600, "candidate_match": 800,
                "private_task": PRIVATE,
            },
            "private_title": PRIVATE,
        },
    }


def test_readable_partial_explains_coverage_and_named_budgets_without_private_data():
    original = event()
    result = reconciliation_event_summary(original)
    assert result is not None
    assert result["state"] == "Incomplète"
    assert result["code"] == "epub_parser_limit_or_invalid"
    assert "fichiers observés : 320" in result["coverage"]
    assert "fichiers SHA vérifié : 320" in result["coverage"]
    assert "notices examinées : 522" in result["coverage"]
    assert "candidats bibliographiques : 0" in result["coverage"]
    assert "SHA : 8100 ms" in result["timing"]
    assert "EPUB/OPF : 1100 ms" in result["timing"]
    assert "budget de temps de l’inventaire atteint" in result["limits"]
    assert "Zéro candidat n’est pas une preuve d’absence" in result["note"]
    assert PRIVATE not in json.dumps(result, ensure_ascii=False)


def test_complete_without_reasons_does_not_claim_match_evidence():
    result = reconciliation_event_summary(event(status="complete", reasons=[]))
    assert result["state"] == "Terminée"
    assert result["code"] == "none"
    assert "Aucune limite signalée" in result["limits"]
    assert "aucune attribution de droit par simple ISBN/SHA" in result["note"]


def test_search_finds_code_on_loaded_page_but_does_not_export_nested_details():
    raw = event()
    payload = {"events": [raw], "total": 1, "offset": 0}
    selected = sanitized_rows(payload, search="epub_parser_limit_or_invalid", include_detail=True)
    assert len(selected) == 1
    assert selected[0]["reconciliation_code"] == "epub_parser_limit_or_invalid"
    assert selected[0]["reconciliation"]["state"] == "Incomplète"
    assert not sanitized_rows(payload, search="snapshot_time_budget")
    public_export = export_selected_events(selected, {"reconcile-synthetic"})
    assert "private" not in public_export.lower()
    assert "PRIVATE" not in public_export
    assert "reconciliation" not in public_export
    assert "9780306406157" not in public_export


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(module="PLUGIN"),
    lambda row: row.update(source="app_log"),
    lambda row: row["detail"].update(operation=PRIVATE),
    lambda row: row["detail"].update(phase="inventory"),
    lambda row: row["detail"].update(status="complete"),
    lambda row: row["detail"].update(reasons=[]),
    lambda row: row["detail"].update(code=PRIVATE),
    lambda row: row["detail"].update(schema_version=True),
    lambda row: row.update(detail=PRIVATE),
])
def test_wrong_or_untrusted_event_never_gets_reconciliation_attribution(mutation):
    raw = event()
    mutation(raw)
    assert reconciliation_event_summary(raw) is None
    rows = sanitized_rows({"events": [raw]})
    assert len(rows) == 1
    assert "reconciliation_code" not in rows[0]


def test_unknown_reason_is_generic_never_echoed():
    raw = event(reasons=[PRIVATE])
    result = reconciliation_event_summary(raw)
    assert result is not None
    assert result["code"] == "unclassified_limit"
    assert result["limits"] == "Causes : cause non classée"
    assert PRIVATE not in json.dumps(result)


def test_unmeasured_counts_remain_unknown_not_zero():
    raw = event(counts={"files_started": True, "files_observed": -1,
                        "files_hashed": PRIVATE, "metadata_candidates": 0},
                phase_ms={"sha256": True, "epub_opf": -1, "structure": PRIVATE})
    result = reconciliation_event_summary(raw)
    assert "fichiers observés" not in result["coverage"]
    assert "fichiers SHA" not in result["coverage"]
    assert "candidats bibliographiques : 0" in result["coverage"]
    assert "SHA :" not in result["timing"]
    assert "EPUB/OPF" not in result["timing"]
    assert PRIVATE not in json.dumps(result)


def test_page_source_contains_direct_readable_summary_not_private_core_import():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    page = (root / "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    explorer = (root / "diagnostics_explorer.py").read_text(encoding="utf-8")
    assert 'row.get("reconciliation")' in page
    assert "Code : {reconciliation['code']}" in page
    assert "code" in explorer
    assert "cifereca.core" not in page
    assert "cifereca.core" not in explorer
