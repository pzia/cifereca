"""P1 #3195/#3132: canonical journal's pre-page matches survive GTK selection.

A Core journal query is the authority for source=journal. No HTTP, GUI
initialization or provider interactions.
"""
from __future__ import annotations

import json

from cifereca.core.diagnostic_journal import query_journal
from diagnostics_explorer import (
    export_selected_events, sanitized_rows, selectable_event_ids,
)
from diagnostics_selection import page_selection_ids


def test_detail_only_global_match_survives_selection_and_clipboard(tmp_path):
    payload = {
        "schema_version": 1,
        "category": "LOAN",
        "operation": "stage_loan_token",
        "status": "failed",
        "message": "Étape de téléchargement terminée",
        "detail": {
            "provider_id": "bnf-pret",
            "phase": "provider_delivery",
            "reason_code": "bnf_http_unavailable",
        },
        "task_ref": "abcdef123456",
    }
    path = tmp_path / "cifereca.log"
    path.write_text(
        "2026-10-26 09:00:00,123 ERROR cifereca [MainThread] DIAG_JSON "
        + json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    for term in ("bnf_http_unavailable", "provider_delivery", "stage_loan_token",
                 "abcdef123456", "BNF-PRET"):
        response = query_journal(path, search=term)
        assert response["total"] == 1
        # GTK's selection and preview paths pass the same search text to
        # sanitized_rows(). It must not silently re-filter a Core match
        # using only visible message/module/severity fields.
        rows = sanitized_rows(response, search=term)
        assert len(rows) == 1
        selected = page_selection_ids(rows, set(), "all")
        assert selected == selectable_event_ids(rows)
        clip = json.loads(export_selected_events(rows, selected))
        assert len(clip["events"]) == 1
        assert clip["events"][0]["event_id"] == rows[0]["event_id"]
        assert response["events"][0]["event_id"] == rows[0]["event_id"]


def test_legacy_source_search_stays_local_to_loaded_page():
    payload = {"events": [
        {"source": "app_log", "timestamp": "2026-10-26T09:00:00+00:00",
         "module": "cifereca", "severity": "error", "message": "irrelevant",
         "event_id": "legacy-a"},
        {"source": "app_log", "timestamp": "2026-10-26T09:00:01+00:00",
         "module": "cifereca", "severity": "error", "message": "HTTP 403",
         "event_id": "legacy-b"},
    ]}
    assert [x["event_id"] for x in sanitized_rows(payload, search="403")] == ["legacy-b"]


def test_journal_nonmatching_rows_are_not_resurrected_by_global_search(tmp_path):
    path = tmp_path / "cifereca.log"
    path.write_text(
        "2026-10-26 09:00:00,123 INFO cifereca [MainThread] unrelated\n",
        encoding="utf-8",
    )
    page = query_journal(path, search="ACSM")
    assert page["total"] == 0
    assert sanitized_rows(page, search="ACSM") == []
