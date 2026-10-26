"""#3108: page-local cover-failure evidence without fake HTTP counts."""
from __future__ import annotations

import pytest

from diagnostics_explorer import (
    sanitized_rows, published_cover_failure, cover_failure_page_summary,
    explorer_header, PAGE_SIZE,
)

WHEN = "2026-10-26T08:00:00+00:00"
MESSAGES = {
    "http_status": "couverture · échec HTTP",
    "timeout": "couverture · délai dépassé",
    "invalid_image": "couverture · image invalide",
}
PRIVATE = "https://private.invalid/loan/title?token=NEVER_ATTEST"


def record(index, *, source="journal", code="http_status"):
    row = {
        "timestamp": WHEN, "source": source, "module": "COVER",
        "severity": "error", "status": "error",
        "event_id": f"{source}-{index}", "message": MESSAGES[code],
        "detail": {"code": code},
    }
    if source == "journal":
        row.update(logger="cifereca", operation="cover_download_failure")
    elif source == "app_log":
        row["module"] = "cifereca"
    return row


def payload(events, source="journal"):
    return {
        "events": events, "sources": [{"source": source, "status": "available"}],
        "total": len(events), "offset": 0,
    }


def test_twelve_cover_failures_are_page_observations_not_http_attempts():
    events = [record(i) for i in range(12)]
    rows = sanitized_rows(payload(events))
    assert len(rows) == 12
    assert all(r["cover_reason"] == "http_status" for r in rows)
    label = explorer_header(payload(events), rows)
    assert "Couvertures : 12 événement(s) observé(s) sur cette page" in label
    assert "échec HTTP : 12" in label
    assert "requêtes HTTP distinctes inconnu" in label
    assert len({r["event_id"] for r in rows}) == 12


def test_legacy_both_channels_do_not_double_count_12_core_events():
    events = [record(i, source="core_events") for i in range(12)]
    events += [record(i, source="app_log") for i in range(12)]
    rows = sanitized_rows(payload(events, "core_events"))
    assert len(rows) == 24
    assert sum("cover_reason" in r for r in rows) == 12
    assert "Couvertures : 12 événement(s)" in cover_failure_page_summary(rows)
    assert "24 événement(s)" not in cover_failure_page_summary(rows)


@pytest.mark.parametrize("mutation", [
    {"source": "app_log"},
    {"module": "PLUGIN"},
    {"logger": "some_plugin"},
    {"operation": "download"},
    {"message": "download"},
    {"status": "ok"},
    {"severity": "warning"},
    {"detail": {"code": "http_status", "url": PRIVATE}},
    {"detail": {"code": "403"}},
    {"detail": {"code": object()}},
    {"detail": PRIVATE},
])
def test_forged_or_generic_journal_cover_is_never_counted(mutation):
    fake = dict(record(1), **mutation)
    assert published_cover_failure(fake) is None
    rows = sanitized_rows(payload([fake]))
    assert len(rows) == 1
    assert "cover_reason" not in rows[0]
    assert cover_failure_page_summary(rows) == ""


def test_mixed_reasons_and_rotation_partial_never_claim_exhaustiveness():
    events = [
        record(1, code="http_status"), record(2, code="timeout"),
        record(3, code="invalid_image"),
    ]
    source = payload(events)
    source["sources"][0]["status"] = "partial"
    source["sources"][0]["reason"] = "rotation_or_read_gap"
    rows = sanitized_rows(source)
    label = explorer_header(source, rows)
    assert "Couvertures : 3 événement(s)" in label
    assert "échec HTTP : 1" in label and "délai dépassé : 1" in label
    assert "image invalide : 1" in label
    assert "requêtes HTTP distinctes inconnu" in label


def test_count_is_bounded_to_loaded_page_not_global_total():
    source = payload([record(i) for i in range(PAGE_SIZE + 10)])
    rows = sanitized_rows(source)
    assert len(rows) == PAGE_SIZE
    summary = cover_failure_page_summary(rows)
    assert f"Couvertures : {PAGE_SIZE} événement(s)" in summary
    assert "60 événement(s)" not in summary
    assert "sur cette page" in summary


def test_bare_reason_in_sanitized_row_does_not_create_spurious_count():
    rows = [{"source": "journal", "module": "COVER", "status": "error",
             "message": "download", "cover_reason": "http_status"}]
    assert cover_failure_page_summary(rows) == ""
