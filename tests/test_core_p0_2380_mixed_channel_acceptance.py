"""P0 #2380: mixed, synthetic, privacy-safe acceptance across local channels.

Two Core-attested connection refusals, three ADEPT runtime facts and one
Numilog terminal error coexist without conflating accounts, worker outcomes
or event IDs. The separate provenance tests validate installed providers.
"""
from __future__ import annotations

import json
import logging

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
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
