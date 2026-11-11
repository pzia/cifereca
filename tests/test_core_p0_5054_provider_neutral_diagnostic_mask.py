"""Core diagnostic disclosure is based on verified producer provenance.

No supplier name or category is authority for exporting even operational text.
Kobo's verified, typed Core backup observation remains separately admissible.
"""
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics


@pytest.mark.parametrize("category", ["NUMILOG", "KOBO", "PARIS", "FUTURE_VENDOR"])
@pytest.mark.parametrize("operation", [
    "session_state", "acquisition_download", "retry_error",
])
def test_unverified_supplier_category_keeps_private_payload_masked(
    tmp_path, category, operation,
):
    secret = "PRIVATE://account/ABCD?token=SECRET"
    diagnostics = Diagnostics(level="trace")
    diagnostics.add(
        category, operation, "error", detail={
            "provider_id": category.lower(), "record_id": secret,
            "account_id": secret, "token": secret,
        },
    )
    report = query_diagnostic_events(
        diagnostics, source="core_events", log_path=tmp_path / "absent",
    )
    event, = report["events"]
    assert event["message"] == "[opération masquée]"
    assert "provider_id" not in event
    assert "detail" not in event
    assert secret not in json.dumps(report)


def test_verified_core_kobo_backup_still_emits_typed_public_observation(tmp_path):
    diagnostics = Diagnostics(level="detailed")
    diagnostics.add_core_kobo_backup_decision({
        "status": "partial", "persisted_enabled": True,
        "connected_kobo": 1, "eligible_kobo": 1, "interval_minutes": 60,
    })
    report = query_diagnostic_events(
        diagnostics, source="core_events", log_path=tmp_path / "absent",
    )
    event, = report["events"]
    assert event["provider_id"] == "kobo"
    assert event["module"] == "KOBO"
    assert event.get("detail", {}).get("status") == "partial"
    assert event["message"] != "[opération masquée]"
