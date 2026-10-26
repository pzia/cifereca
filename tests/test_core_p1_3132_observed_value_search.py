"""#3132: reader-generated journal attribute NAMES are not observed log text.

One physical local synthetic file; no producer activation, UI or network.
"""
from __future__ import annotations

import json

from cifereca.core.diagnostic_journal import query_journal


PREFIX = "2026-10-26 10:00:00,123 ERROR cifereca [MainThread] "


def test_unstructured_line_does_not_match_generated_envelope_field_names(tmp_path):
    path = tmp_path / "cifereca.log"
    path.write_text(
        PREFIX + "BNF prêt ACSM HTTP 403 pendant le téléchargement\n",
        encoding="utf-8",
    )
    expected_absent = (
        "event_id", "schema_version", "source", "journal", "logger",
        "module", "severity", "message", "status", "operation",
        "duration_ms", "task_ref", "detail", "unknown",
    )
    for term in expected_absent:
        assert query_journal(path, search=term)["total"] == 0, term
    for observed in ("BNF", "pret", "ACSM", "HTTP", "403", "cifereca", "error"):
        assert query_journal(path, search=observed)["total"] == 1, observed


def test_source_provided_structured_field_values_and_keys_remain_searchable(tmp_path):
    path = tmp_path / "cifereca.log"
    payload = {
        "schema_version": 1,
        "category": "LOAN",
        "message": "Récupération effectuée",
        "operation": "download_loan",
        "status": "failed",
        "task_ref": "abcdef123456",
        "duration_ms": 132,
        "detail": {
            "provider_id": "bnf-pret",
            "reason_code": "bnf_http_unavailable",
            "phase": "stage_loan_token",
            "extra_source_label": "message",
        },
    }
    path.write_text(
        PREFIX + "DIAG_JSON " + json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    for term in (
        "download_loan", "failed", "abcdef123456", "132", "provider_id",
        "bnf-pret", "reason_code", "bnf_http_unavailable", "phase",
        "stage_loan_token", "extra_source_label", "message", "LOAN",
        "recuperation",
    ):
        assert query_journal(path, search=term)["total"] == 1, term
    assert query_journal(path, search="logger")["total"] == 0
    assert query_journal(path, search="event_id")["total"] == 0


def test_search_filter_then_page_remains_correct_without_envelope_false_positives(tmp_path):
    path = tmp_path / "cifereca.log"
    path.write_text(
        PREFIX + "premier\n"
        + PREFIX + "module est un mot source réel\n"
        + PREFIX + "troisième\n",
        encoding="utf-8",
    )
    result = query_journal(path, search="module", limit=1)
    assert result["total"] == 1
    assert result["has_more"] is False
    assert result["events"][0]["message"] == "module est un mot source réel"
    assert query_journal(path, search="module", limit=1, offset=1)["events"] == []
