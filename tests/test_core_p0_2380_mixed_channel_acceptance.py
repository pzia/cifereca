"""P0 #2380: mixed, synthetic, privacy-safe acceptance across local channels.

Two Core-attested connection refusals, three ADEPT runtime facts and one
Numilog terminal error coexist without conflating accounts, worker outcomes
or event IDs. The separate provenance tests validate installed providers.
"""
from __future__ import annotations

from collections import Counter
import json
import logging
from types import SimpleNamespace as NS

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.numilog_startup_diagnostics import StartupTrace
from cifereca.diagnostics import Diagnostics


PRIVATE = "UNPUBLISHABLE https://private.invalid/LOAN/link.acsm?token=SECRETTOKEN"
ADEPT_CODES = (
    "adept_error", "profile_missing", "kobo_adept_timeout",
)


@pytest.fixture
def log_path(tmp_path):
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    previous = logger.level
    handler = logging.FileHandler(path)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield path
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()


def _facts(row):
    detail = row.get("detail")
    if type(detail) is not dict:
        return None
    if detail.get("code") == "provider_connect_failed":
        return "provider_connection", detail
    if detail.get("operation_kind") == "kobo_adept":
        return "kobo_adept", detail
    if detail.get("task_kind") == "source-materialize":
        return "numilog_startup", detail
    return None


def test_mixed_core_evidence_survives_both_channels_without_secrets(log_path):
    diagnostics = Diagnostics(level="detailed")

    # These are Core-attested *synthetic* results, not arbitrary plugin.add
    # calls and not a simulated authentication request.
    diagnostics.add_core_provider_connect_failure("provider.test")
    diagnostics.add_core_provider_connect_failure("source_inconnue")
    for code in ADEPT_CODES:
        diagnostics.add_core_kobo_adept(
            {
                "operation": "download request",
                "error_code": code,
                "retryable": False,
                "duration_ms": 14,
                "returncode": 1,
                "stdout_summary": PRIVATE,
                "stderr_summary": PRIVATE,
                "exception": PRIVATE,
                "account_id": PRIVATE,
            },
            "error",
        )

    # The source id is intentionally NOT a writable scoped source; its
    # diagnostic terminal failure can never save a receipt or invoke a GET.
    service = NS(
        diagnostics=diagnostics,
        storage=NS(metadata_get=lambda _key: None),
    )
    startup = StartupTrace(service, "synthetic-not-a-source-id")
    startup.phase = "acquisition"
    startup.fail("acquisition_failed")
    startup.counts.update({"attempted": 1, "failed": 1})
    numilog = startup.finish("failed")
    assert numilog["code"] == "acquisition_failed"

    report = query_diagnostic_events(diagnostics, log_path=log_path, limit=100)
    channels = {}
    for channel in ("core_events", "app_log"):
        rows = [r for r in report["events"] if r["source"] == channel]
        observed = [_facts(row) for row in rows]
        evidence = [(kind, json.dumps(fields, sort_keys=True))
                    for item in observed if item is not None
                    for kind, fields in (item,)]
        channels[channel] = Counter(evidence)
        assert sum(kind == "provider_connection" for kind, _ in evidence) == 2
        assert sum(kind == "kobo_adept" for kind, _ in evidence) == 3
        assert sum(kind == "numilog_startup" for kind, _ in evidence) == 1
        assert all("task_ref" not in r
                   and "task_ref" not in r.get("detail", {}) for r in rows)
    assert channels["core_events"] == channels["app_log"]
    assert len({r["event_id"] for r in report["events"]}) == report["total"]
    # Distinct event IDs do not establish cross-source causality.
    assert report["total"] == 12
    published = json.dumps(report) + log_path.read_text()
    for secret in ("UNPUBLISHABLE", "private.invalid", "SECRETTOKEN",
                   "/LOAN/link.acsm", "account_id", "stdout_summary"):
        assert secret not in published


def test_unrelated_coincident_events_do_not_gain_a_task_reference(log_path):
    diagnostics = Diagnostics()
    diagnostics.add_core_provider_connect_failure("provider.test")
    diagnostics.add_core_kobo_adept(
        {"operation": "download request", "error_code": "adept_error",
         "returncode": 1, "task_ref": "aaaaaaaaaaaa"},
        "error",
    )
    result = query_diagnostic_events(diagnostics, log_path=log_path, limit=100)
    assert len(result["events"]) == 4
    assert all("task_ref" not in event for event in result["events"])
    assert all("task_ref" not in event.get("detail", {})
               for event in result["events"])
