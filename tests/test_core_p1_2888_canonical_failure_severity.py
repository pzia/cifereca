"""P1 #2888: failed and refused loan downloads appear in canonical Errors.

Core-attested synthetic terminal outcomes only. No provider HTTP or downloads.
"""
from __future__ import annotations

import logging

from cifereca.core.diagnostic_journal import query_journal
from cifereca.diagnostics import Diagnostics
from cifereca import logging_utils


CASES = (
    ("failed", "provider_failure", "error"),
    ("refused", "download_unsupported", "error"),
    ("attention_required", "adept_identity_unproven", "warning"),
    ("retryable_error", "provider_unavailable", "warning"),
    ("cancelled", "operation_cancelled", "warning"),
    ("success", "completed", "info"),
)


def test_canonical_error_filter_contains_explicit_download_failures(tmp_path):
    path = tmp_path / "cifereca.log"
    handler = logging_utils.PrivateRotatingFileHandler(
        path, maxBytes=100_000, backupCount=2, encoding="utf-8",
    )
    handler.setFormatter(logging_utils.CanonicalEventFormatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s",
    ))
    logger = logging.getLogger("cifereca")
    original = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        producer = Diagnostics(enabled=False, level="errors")
        for status, code, _severity in CASES:
            producer.add_core_loan_download({
                "schema_version": 1, "provider_id": "paris",
                "operation_kind": "download_loan",
                "phase": "terminal", "status": status,
                "code": code, "duration_ms": 42,
            })
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original)
        handler.close()

    results = query_journal(path, provider_id="paris", limit=100)
    assert results["total"] == len(CASES)
    events = {event["detail"]["status"]: event for event in results["events"]}
    for status, code, severity in CASES:
        assert events[status]["severity"] == severity
        assert events[status]["operation"] == "download_loan"
        assert events[status]["detail"]["code"] == code
        assert events[status]["module"] == "PNB"
    failures = query_journal(path, provider_id="paris", severity="error")
    assert failures["total"] == 2
    assert {event["status"] for event in failures["events"]} == {
        "failed", "refused",
    }
    warnings = query_journal(path, provider_id="paris", severity="warning")
    assert warnings["total"] == 3
    assert {e["status"] for e in warnings["events"]} == {
        "attention_required", "retryable_error", "cancelled",
    }
    assert query_journal(path, provider_id="paris", severity="info")["total"] == 1


def test_unqualified_event_is_not_published_as_download_failure(tmp_path):
    path = tmp_path / "cifereca.log"
    handler = logging_utils.PrivateRotatingFileHandler(
        path, maxBytes=100_000, backupCount=1, encoding="utf-8",
    )
    handler.setFormatter(logging_utils.CanonicalEventFormatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s",
    ))
    logger = logging.getLogger("cifereca")
    original = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        Diagnostics(enabled=False).add_core_loan_download({
            "schema_version": 1, "provider_id": "paris",
            "operation_kind": "download_loan",
            "phase": "terminal", "status": "failed",
            "code": "completed",  # Contradicts a terminal failure.
            "duration_ms": 42,
        })
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original)
        handler.close()
    assert query_journal(path, severity="error")["total"] == 0
