"""#2698: private exports publish closed outcomes, never capture contents."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.browser_recorder_diagnostics import (
    PLUGIN_ID, OPERATIONS, public_fields, verified_fields,
)
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics
from cifereca.plugins.manager import PluginManager


def manager(collector, package="org.cifereca.technical.browser-recorder"):
    record = NS(descriptor=NS(id=PLUGIN_ID), package=NS(manifest=NS(package=NS(id=package))))
    return NS(diagnostics=collector, _records={PLUGIN_ID: record})


@pytest.mark.parametrize("code,status,severity", [
    ("private_zip_exported", "ok", "info"),
    ("private_zip_export_failed", "error", "error"),
    ("private_zip_export_cancelled", "warning", "warning"),
])
def test_private_export_through_scoped_context_collector_and_public_feed(tmp_path, code, status, severity):
    collector = Diagnostics()
    secret = "https://private.invalid/?token=FICTIONAL_PRIVATE_ZIP_PASSWORD"
    PluginManager._plugin_diag(manager(collector), PLUGIN_ID, secret, "error", {
        "operation": "private_zip_export", "code": code,
        "duration_ms": 412, "output_bytes": 1234, "path": secret, "passphrase": secret,
    }, "normal")
    snapshot = collector.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0]["status"] == status
    assert snapshot[0]["duration_ms"] == 412
    assert snapshot[0]["verified_browser_recorder"] is True
    events = query_diagnostic_events(collector, log_path=tmp_path / "absent", source="core_events")
    row = events["events"][0]
    assert row["severity"] == severity
    assert row["detail"] == {
        "plugin_id": PLUGIN_ID, "operation": "private_zip_export", "code": code,
        "duration_ms": 412, "output_bytes": 1234,
    }
    for rendered in (repr(snapshot), repr(events), collector.text("test")):
        assert "FICTIONAL_PRIVATE_ZIP_PASSWORD" not in rendered
        assert "private.invalid" not in rendered


@pytest.mark.parametrize("bad", [True, -1, 1.5, "PRIVATE", 1 << 80, None])
def test_private_metrics_reject_coercion_and_invalid_values(bad):
    fields = public_fields("private_zip_export", "private_zip_exported", duration_ms=bad, output_bytes=bad)
    assert set(fields) == {"plugin_id", "operation", "code"}
    detail = dict(fields, duration_ms=bad)
    assert verified_fields(detail, OPERATIONS["private_zip_export"], "ok") is None


def test_hostile_metrics_never_stringified_and_non_private_events_keep_old_shape():
    class Hostile:
        def __str__(self):
            raise AssertionError("private value coerced")
        def __repr__(self):
            raise AssertionError("private value rendered")
    fields = public_fields("private_zip_export", "private_zip_exported", duration_ms=Hostile(), output_bytes=Hostile())
    assert len(fields) == 3
    old = public_fields("zip_export", "zip_export_failed", duration_ms=12, output_bytes=44)
    assert len(old) == 3
    assert verified_fields(dict(old, output_bytes=44), OPERATIONS["zip_export"], "error") is None


def test_metric_boundaries_and_old_private_results_remain_valid():
    for metrics in ({}, {"duration_ms": 0, "output_bytes": 0},
                    {"duration_ms": 86_400_000, "output_bytes": 16 * 1024 * 1024}):
        fields = public_fields("private_zip_export", "private_zip_exported", **metrics)
        assert verified_fields(fields, OPERATIONS["private_zip_export"], "ok") == fields
    for key, limit in (("duration_ms", 86_400_000), ("output_bytes", 16 * 1024 * 1024)):
        fields = public_fields("private_zip_export", "private_zip_exported", **{key: limit + 1})
        assert key not in fields


def test_errors_level_and_provenance_cannot_be_forged(tmp_path):
    collector = Diagnostics(level="errors")
    for code in ("private_zip_exported", "private_zip_export_failed", "private_zip_export_cancelled"):
        PluginManager._plugin_diag(manager(collector), PLUGIN_ID, "ignored", "ok", {
            "operation": "private_zip_export", "code": code,
        }, "normal")
    assert [r["status"] for r in collector.snapshot()] == ["error", "warning"]
    collector = Diagnostics()
    fields = public_fields("private_zip_export", "private_zip_exported", output_bytes=123)
    collector.add("PLUGIN", OPERATIONS["private_zip_export"], "ok", detail=fields)
    events = query_diagnostic_events(collector, log_path=tmp_path / "absent", source="core_events")
    assert events["events"][0].get("detail", {}) == {}
    collector = Diagnostics()
    PluginManager._plugin_diag(manager(collector, package="org.untrusted.plugin"), PLUGIN_ID, "ignored", "ok", fields, "normal")
    assert collector.snapshot() == []
