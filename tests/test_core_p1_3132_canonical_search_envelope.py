"""#3132: search canonical journal observations, not its generated envelope.

Physical local synthetic lines only; no provider I/O, credentials or exports.
"""
from __future__ import annotations

import json

from cifereca.core.diagnostic_journal import query_journal


PREFIX = "2026-10-26 08:00:00,100 INFO cifereca [MainThread] "
SECOND = "2026-10-26 08:00:00,101 ERROR cifereca [MainThread] "


def _structured(*, detail):
    return "DIAG_JSON " + json.dumps({
        "schema_version": 1,
        "message": "Échec emprunt",
        "category": "LOAN",
        "operation": "loan_download",
        "status": "failed",
        "detail": detail,
    }, ensure_ascii=False)


def test_generated_source_schema_and_event_id_are_not_fake_matches(tmp_path):
    log = tmp_path / "cifereca.log"
    log.write_text(
        PREFIX + "Bibliothèque locale accessible\n"
        + SECOND + _structured(detail={
            "provider_id": "bnf-pret", "reason_code": "provider_unavailable",
        }) + "\n",
        encoding="utf-8",
    )

    baseline = query_journal(log)
    assert baseline["total"] == 2
    ids = {item["event_id"] for item in baseline["events"]}
    assert len(ids) == 2
    assert {item["source"] for item in baseline["events"]} == {"journal"}

    # These keys/values did not occur in either ORIGINAL message or payload.
    for synthetic in ("journal", "event_id", "schema_version"):
        result = query_journal(log, search=synthetic)
        assert result["total"] == 0, synthetic
    assert query_journal(log, search=next(iter(ids)))["total"] == 0

    # Original text, structured provider, operation, reason, severity and
    # timestamp are still available; filters always run before pagination.
    assert query_journal(log, search="Bibliotheque")["total"] == 1
    assert query_journal(log, search="bnf-pret")["total"] == 1
    assert query_journal(log, search="provider_unavailable")["total"] == 1
    assert query_journal(log, search="loan_download")["total"] == 1
    assert query_journal(log, search="error")["total"] == 1
    assert query_journal(log, search="2026-10-26")["total"] == 2
    assert query_journal(log, search="bnf-pret", provider_id="bnf-pret", limit=1)["total"] == 1


def test_identical_word_in_original_structured_payload_is_searchable(tmp_path):
    log = tmp_path / "cifereca.log"
    log.write_text(
        PREFIX + "Pas de texte correspondant\n"
        + SECOND + _structured(detail={
            "provider_id": "paris",
            "note": "Le journal réseau est disponible",
            "schema_version": "source-provided",
        }) + "\n",
        encoding="utf-8",
    )

    for original in ("journal", "schema_version", "source-provided", "paris"):
        result = query_journal(log, search=original)
        assert result["total"] == 1, original
        assert result["events"][0]["detail"]["provider_id"] == "paris"
    assert query_journal(log, search="journal", offset=1)["events"] == []
