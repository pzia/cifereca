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
    assert published["read_only"] is True
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


@pytest.mark.parametrize(("operation", "code"), [
    ("http_capture", "capture_failed"),
    ("http_capture", "capture_interrupted"),
    ("http_capture", "capture_limit"),
    ("http_review", "review_failed"),
    ("zip_prepare", "zip_prepare_failed"),
    ("zip_export", "zip_export_failed"),
])
def test_scoped_browser_recorder_runtime_failures_use_attested_public_feed(
    tmp_path, operation, code,
):
    from cifereca.plugins.manager import PluginManager

    installed = SimpleNamespace(
        descriptor=SimpleNamespace(id=PLUGIN_ID),
        package=SimpleNamespace(
            manifest=SimpleNamespace(
                package=SimpleNamespace(
                    id="org.cifereca.technical.browser-recorder",
                ),
            ),
        ),
    )
    collector = Diagnostics()
    manager = SimpleNamespace(
        diagnostics=collector, _records={PLUGIN_ID: installed},
    )
    fake_secret = "https://proxy.invalid/?token=FAKE_PRIVATE_ZIP_PASSWORD"
    PluginManager._plugin_diag(
        manager, PLUGIN_ID, fake_secret, "error",
        {"operation": operation, "code": code, "raw_capture": fake_secret},
        "normal",
    )
    result = query_diagnostic_events(
        collector, log_path=tmp_path / "absent",
        source="core_events", module="PLUGIN", severity="error",
    )
    assert result["total"] == 1
    row = result["events"][0]
    assert row["detail"] == {
        "plugin_id": PLUGIN_ID, "operation": operation, "code": code,
    }
    assert collector.snapshot()[0]["verified_browser_recorder"] is True
    for rendered in (repr(result), repr(collector.snapshot()), collector.text("test")):
        assert "FAKE_PRIVATE_ZIP_PASSWORD" not in rendered
        assert "proxy.invalid" not in rendered


def test_runtime_cannot_forge_core_chromium_status_or_unverified_package(tmp_path):
    from cifereca.plugins.manager import PluginManager

    secret = "PRIVATE_TOKEN_NEVER_EXPORT"
    class Hostile:
        def __str__(self):
            raise AssertionError("unexpected coercion of private value")
        def __repr__(self):
            raise AssertionError("unexpected representation")

    def runtime(package_id):
        return SimpleNamespace(
            descriptor=SimpleNamespace(id=PLUGIN_ID),
            package=SimpleNamespace(
                manifest=SimpleNamespace(
                    package=SimpleNamespace(id=package_id),
                ),
            ),
        )

    collector = Diagnostics()
    manager = SimpleNamespace(
        diagnostics=collector,
        _records={
            PLUGIN_ID: runtime("org.cifereca.technical.browser-recorder"),
        },
    )
    # Scoped plugin cannot claim success of Core-owned installation/probe.
    PluginManager._plugin_diag(
        manager, PLUGIN_ID, secret, "ok",
        {"operation": "chromium_install", "code": "ready", "url": secret},
        "normal",
    )
    assert collector.snapshot() == []
    # A hostile or unsupported code becomes a fixed, non-leaking error.
    PluginManager._plugin_diag(
        manager, PLUGIN_ID, secret, "error",
        {"operation": "http_capture", "code": Hostile(), "url": secret},
        "normal",
    )
    assert collector.snapshot()[0]["detail"]["code"] == "browser_operation_failed"

    # A package with a forged plugin identity is not an attested producer.
    manager._records[PLUGIN_ID] = runtime("org.example.unverified")
    PluginManager._plugin_diag(
        manager, PLUGIN_ID, secret, "error",
        {"operation": "zip_export", "code": "zip_export_failed", "url": secret},
        "normal",
    )
    result = query_diagnostic_events(
        collector, log_path=tmp_path / "absent",
        source="core_events", module="PLUGIN",
    )
    assert result["total"] == 1
    assert secret not in repr(result)


@pytest.mark.parametrize(("operation", "invalid_code"), [
    ("http_capture", "zip_export_failed"),
    ("http_review", "capture_failed"),
    ("zip_prepare", "review_failed"),
    ("zip_export", "playwright_missing"),
])
def test_plugin_runtime_cannot_attest_a_code_for_the_wrong_action(
    tmp_path, operation, invalid_code,
):
    """A real recorder can report only fixed outcomes appropriate to its action."""
    from cifereca.plugins.manager import PluginManager

    secret = "FAKE_PRIVATE_HTTP_COOKIE_NEVER_PUBLISH"
    collector = Diagnostics()
    manager = SimpleNamespace(
        diagnostics=collector,
        _records={
            PLUGIN_ID: SimpleNamespace(
                descriptor=SimpleNamespace(id=PLUGIN_ID),
                package=SimpleNamespace(
                    manifest=SimpleNamespace(
                        package=SimpleNamespace(
                            id="org.cifereca.technical.browser-recorder",
                        ),
                    ),
                ),
            ),
        },
    )
    PluginManager._plugin_diag(
        manager, PLUGIN_ID, "https://example.invalid/?token=" + secret,
        "ok", {"operation": operation, "code": invalid_code, "url": secret},
        "normal",
    )
    rows = collector.snapshot()
    assert len(rows) == 1
    assert rows[0]["status"] == "error"
    assert rows[0]["detail"] == {
        "plugin_id": PLUGIN_ID,
        "operation": operation,
        "code": "browser_operation_failed",
    }
    public = query_diagnostic_events(
        collector, log_path=tmp_path / "absent",
        source="core_events", module="PLUGIN", severity="error",
    )
    assert public["total"] == 1
    assert public["events"][0]["detail"] == rows[0]["detail"]
    assert secret not in repr(public)
    assert secret not in collector.text("unit")


@pytest.mark.parametrize(("operation", "code", "expected"), [
    ("har_record", "har_record_failed", "har_record_failed"),
    ("har_record", "capture_interrupted", "capture_interrupted"),
    ("har_record", "playwright_missing", "playwright_missing"),
    ("har_replay", "har_replay_failed", "har_replay_failed"),
    ("har_replay", "playwright_missing", "playwright_missing"),
    ("har_record", "har_replay_failed", "browser_operation_failed"),
    ("har_replay", "capture_interrupted", "browser_operation_failed"),
    ("har_replay", "zip_export_failed", "browser_operation_failed"),
])
def test_legacy_har_runtime_diagnostics_attested_per_operation(
    tmp_path, operation, code, expected,
):
    """HAR user actions publish only package-bound closed Core vocabulary."""
    from cifereca.plugins.manager import PluginManager

    collector = Diagnostics()
    manager = SimpleNamespace(
        diagnostics=collector,
        _records={
            PLUGIN_ID: SimpleNamespace(
                descriptor=SimpleNamespace(id=PLUGIN_ID),
                package=SimpleNamespace(
                    manifest=SimpleNamespace(
                        package=SimpleNamespace(
                            id="org.cifereca.technical.browser-recorder",
                        ),
                    ),
                ),
            ),
        },
    )
    secret = "FAKE_HAR_AUTH_PASSWORD_NEVER_PUBLISH"
    PluginManager._plugin_diag(
        manager, PLUGIN_ID, "https://example.invalid/?password=" + secret,
        "error",
        {
            "operation": operation,
            "code": code,
            "request_body": secret,
            "signed_url": "https://example.invalid/?token=" + secret,
        },
        "normal",
    )
    rows = collector.snapshot()
    assert len(rows) == 1
    assert rows[0]["verified_browser_recorder"] is True
    assert rows[0]["detail"] == {
        "plugin_id": PLUGIN_ID, "operation": operation, "code": expected,
    }
    projected = query_diagnostic_events(
        collector, log_path=tmp_path / "absent",
        source="core_events", module="PLUGIN", severity="error",
    )
    assert projected["total"] == 1
    assert projected["events"][0]["detail"] == rows[0]["detail"]
    assert secret not in repr(projected)
    assert secret not in collector.text("unit")
