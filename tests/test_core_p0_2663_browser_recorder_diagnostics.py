"""#2663 P0: fixed, attested Browser Recorder results across Core Diagnostics.

No browser, vendor site, real account, process, network call or private trace.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca.core.browser_recorder_diagnostics import (
    OPERATIONS, PLUGIN_ID, public_fields,
)
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics
from cifereca.gui.browser_recorder_diagnostics import (
    emit_browser_recorder_diagnostic,
)


@pytest.mark.parametrize(("operation", "code", "status", "severity"), [
    ("python_dependencies_install", "python_ready", "ok", "info"),
    ("python_dependencies_install", "python_install_failed", "error", "error"),
    ("chromium_probe", "playwright_missing", "warning", "warning"),
    ("chromium_probe", "chromium_missing", "warning", "warning"),
    ("chromium_probe", "chromium_revision_incompatible", "warning", "warning"),
    ("chromium_probe", "browser_cache_unavailable", "error", "error"),
    ("chromium_probe", "linux_libraries_missing", "error", "error"),
    ("chromium_probe", "browser_launch_failed", "error", "error"),
    ("chromium_install", "download_failed", "error", "error"),
    ("chromium_install", "download_timeout", "error", "error"),
    ("chromium_install", "cancelled", "warning", "warning"),
    ("chromium_install", "ready", "ok", "info"),
])
def test_requested_result_is_visible_via_attested_public_broker_feed(
    tmp_path, operation, code, status, severity,
):
    diagnostics = Diagnostics()
    window = SimpleNamespace(diagnostics=diagnostics)
    assert diagnostics.snapshot() == []  # no event on plugin discovery
    emit_browser_recorder_diagnostic(window, operation, code)
    row = diagnostics.snapshot()
    assert len(row) == 1
    assert row[0]["category"] == "PLUGIN"
    assert row[0]["operation"] == OPERATIONS[operation]
    assert row[0]["status"] == status
    assert row[0]["verified_browser_recorder"] is True
    assert row[0]["detail"] == {
        "plugin_id": PLUGIN_ID, "operation": operation, "code": code,
    }

    published = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "absent",
        source="core_events", module="PLUGIN", severity=severity,
    )
    assert published["total"] == 1
    event = published["events"][0]
    assert event["source"] == "core_events"
    assert event["module"] == "PLUGIN"
    assert event["severity"] == severity
    assert event["message"] == OPERATIONS[operation]
    assert event["detail"] == row[0]["detail"]
    assert event["read_only"] if "read_only" in event else True
    assert "private" not in json.dumps(published).lower()


def test_hostile_values_never_escape_onto_public_event_feed(tmp_path):
    fake_secret = "https://example.invalid/private?token=FAKE_SECRET_NEVER_EXPORT"
    diagnostics = Diagnostics()
    window = SimpleNamespace(diagnostics=diagnostics)
    emit_browser_recorder_diagnostic(window, "chromium_install", fake_secret)
    emit_browser_recorder_diagnostic(window, fake_secret, "download_failed")
    # Exposing an object repr could include proxy auth or an exception.
    class Hostile:
        def __str__(self):
            raise AssertionError("object was stringified")
        def __repr__(self):
            raise AssertionError("object was represented")
    emit_browser_recorder_diagnostic(window, "chromium_probe", Hostile())
    assert [x["detail"]["code"] for x in diagnostics.snapshot()] == [
        "browser_operation_failed", "browser_operation_failed",
    ]
    feed = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "absent", source="core_events",
        module="PLUGIN",
    )
    assert feed["total"] == 2
    for payload in (diagnostics.snapshot(), feed, diagnostics.text("unit")):
        serialized = json.dumps(payload) if not isinstance(payload, str) else payload
        assert "FAKE_SECRET_NEVER_EXPORT" not in serialized
        assert "example.invalid" not in serialized


def test_generic_plugin_record_cannot_forge_browser_result_provenance(tmp_path):
    diagnostics = Diagnostics()
    operation = OPERATIONS["chromium_install"]
    fields = public_fields("chromium_install", "ready")
    diagnostics.add("PLUGIN", operation, "ok", detail=fields)
    feed = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "absent", source="core_events",
        module="PLUGIN",
    )
    assert feed["total"] == 1
    assert feed["events"][0].get("detail") is None
    assert "verified_browser_recorder" not in diagnostics.snapshot()[0]


def test_optional_collector_and_errors_only_level_are_fail_closed(tmp_path):
    emit_browser_recorder_diagnostic(SimpleNamespace(), "chromium_probe", "ready")

    class ExplodingCollector:
        def add_core_browser_recorder_result(self, _operation, _code):
            raise ValueError("https://proxy.invalid/?token=PRIVATE")

    emit_browser_recorder_diagnostic(
        SimpleNamespace(diagnostics=ExplodingCollector()),
        "chromium_probe", "browser_launch_failed",
    )
    diagnostics = Diagnostics(level="errors")
    window = SimpleNamespace(diagnostics=diagnostics)
    emit_browser_recorder_diagnostic(window, "chromium_install", "ready")
    emit_browser_recorder_diagnostic(window, "chromium_install", "download_failed")
    assert len(diagnostics.snapshot()) == 1
    assert diagnostics.snapshot()[0]["detail"]["code"] == "download_failed"
    assert query_diagnostic_events(
        diagnostics, log_path=tmp_path / "none",
        source="core_events", module="PLUGIN", severity="error",
    )["total"] == 1
