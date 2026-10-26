from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.plugin_api import DiagnosticsBroker
from cifereca.system_status import system_status


class Manager:
    def diagnostic(self):
        return [
            {
                "plugin_id": "runtime.kobo-adept",
                "display_name": "Kobo Adept",
                "type": "runtime.adapter",
                "version": "1.2.3",
                "package_id": "runtime.kobo-adept",
                "package_version": "2.4.5",
                "enabled": True,
                "state": "ready",
                "builtin": False,
                "installed_at": "2026-10-07T10:00:00+00:00",
                "reason_code": "",
                "capabilities": ["adept"],
            },
            {
                "plugin_id": "diagnostics.kobo-adept",
                "display_name": "Diagnostic Kobo Adept",
                "type": "diagnostics.check",
                "version": "1.0.0",
                "package_id": "runtime.kobo-adept",
                "package_version": "2.4.5",
                "enabled": True,
                "state": "ready",
                "builtin": False,
                "installed_at": "2026-10-07T10:00:00+00:00",
                "reason_code": "",
                "capabilities": [],
            },
        ]


class Service:
    def __init__(self):
        self.plugin_manager = Manager()
        self.diagnostics = NS(snapshot=lambda: [])

    def runtime_diagnostic(self, plugin_id):
        assert plugin_id == "runtime.kobo-adept"
        return {
            "operation": "download",
            "duration_ms": 42,
            "stdout_bytes": 123,
            "stderr_bytes": 12,
            "stderr_summary": "warning only",
            "returncode": 0,
            "stdout_mode": "json",
            "stdout_summary": "objet JSON",
            "stdin": "SECRET",
            "stdout": '{"token":"SECRET"}',
            "stderr": "SECRET",
            "profile": "alice",
            "loan_id": "urn:secret",
            "payload": {"token": "SECRET"},
        }


def test_diagnostics_protocol_includes_public_inventory_and_runtime_diagnostic():
    broker = DiagnosticsBrokerImpl(Service())

    assert isinstance(broker, DiagnosticsBroker)
    assert callable(broker.inventory)
    assert callable(broker.runtime_diagnostic)


def test_inventory_uses_versions_from_loaded_descriptors():
    rows = DiagnosticsBrokerImpl(Service()).inventory()

    assert rows[0]["plugin_id"] == "runtime.kobo-adept"
    assert rows[0]["version"] == "1.2.3"
    assert rows[0]["package_version"] == "2.4.5"
    assert rows[1]["package_version"] == "2.4.5"


def test_system_status_groups_packages_from_loaded_manifest_rows(monkeypatch):
    monkeypatch.setattr("cifereca.system_status.core_installation_date", lambda: None)

    status = system_status(Manager())

    assert status["packages"] == [{
        "package_id": "runtime.kobo-adept",
        "package_version": "2.4.5",
        "builtin": False,
        "plugins": [
            {"plugin_id": "runtime.kobo-adept", "version": "1.2.3", "state": "ready", "enabled": True},
            {"plugin_id": "diagnostics.kobo-adept", "version": "1.0.0", "state": "ready", "enabled": True},
        ],
    }]


def test_runtime_diagnostic_is_allowlisted_bounded_and_never_exposes_raw_streams_or_ids():
    row = DiagnosticsBrokerImpl(Service()).runtime_diagnostic("runtime.kobo-adept")

    assert row["operation"] == "download"
    assert row["duration_ms"] == 42
    assert row["stdout_mode"] == "json"
    assert row["returncode"] == 0
    for forbidden in ("stdin", "stdout", "stderr", "profile", "loan_id", "payload"):
        assert forbidden not in row


def test_runtime_diagnostic_truncates_text():
    service = Service()
    service.runtime_diagnostic = lambda _plugin_id: {
        "operation": "x" * 1000,
        "stderr_summary": "y" * 1000,
    }

    row = DiagnosticsBrokerImpl(service).runtime_diagnostic("runtime.kobo-adept")

    assert len(row["operation"]) <= 240
    assert len(row["stderr_summary"]) <= 240


def test_inventory_preserves_unmasked_descriptor_values():
    service = Service()
    service.plugin_manager.diagnostic = lambda: [{
        "plugin_id": "runtime.example",
        "display_name": "Reader alice@example.org",
        "type": "runtime.adapter",
        "version": "1.0",
        "reason_code": "https://example.org/item?token=KEEP",
        "capabilities": ["source=https://example.org/item?token=KEEP"],
    }]
    row = DiagnosticsBrokerImpl(service).inventory()[0]
    assert row["display_name"] == "Reader alice@example.org"
    assert row["reason_code"] == "https://example.org/item?token=KEEP"
    assert row["capabilities"] == ("source=https://example.org/item?token=KEEP",)


def test_runtime_diagnostic_preserves_values_without_core_redaction():
    service = Service()
    service.runtime_diagnostic = lambda _plugin_id: {
        "operation": "https://example.org/loan?token=KEEP",
        "stderr_summary": "login=alice@example.org token=KEEP /home/alice/file.epub",
        "stdout_summary": "password=KEEP",
    }
    row = DiagnosticsBrokerImpl(service).runtime_diagnostic("runtime.kobo-adept")
    assert row["operation"] == "https://example.org/loan?token=KEEP"
    assert row["stderr_summary"] == "login=alice@example.org token=KEEP /home/alice/file.epub"
    assert row["stdout_summary"] == "password=KEEP"
