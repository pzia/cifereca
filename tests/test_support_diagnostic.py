from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from cifereca import support_diagnostic


class Storage:
    def metadata_get(self, _key, default=None):
        return default


class Manager:
    def diagnostic(self):
        return [{
            "plugin_id": "plugin.test",
            "package_id": "pkg.test",
            "version": "1.0",
            "package_version": "1.0",
            "enabled": True,
            "state": "degraded",
            "reason_code": "optional_missing",
            "requirements": [],
            "requires": [],
            "requires_contracts": [],
            "error": "token=supersecret",
        }]

    def services(self, category):
        return ()


class Service:
    def __init__(self):
        self.storage = Storage()
        self.accounts = []
        self.plugin_manager = Manager()

    def accounts_for_identity(self, identity_id=None):
        return []


def test_support_report_preserves_original_values_and_checks_schema(tmp_path, monkeypatch):
    db = tmp_path / "library.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("create table metadata(key text primary key, value text not null)")
        conn.execute("insert into metadata values('schema_version', ?)", (str(support_diagnostic.CURRENT_SCHEMA_VERSION),))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"identities": [], "providers": {}, "token": "supersecret"}), encoding="utf-8")
    monkeypatch.setattr(support_diagnostic, "database_file", lambda: db)
    monkeypatch.setattr(support_diagnostic, "config_file", lambda: config)

    report = support_diagnostic.build_support_report(Service(), verbose=True)
    text = support_diagnostic.support_report_text(Service(), verbose=True)

    assert report["database"]["status"] == "ok"
    assert report["database"]["schema"] == support_diagnostic.CURRENT_SCHEMA_VERSION
    assert report["plugins"]["attention"] == 1
    assert report["result"] == "warning"
    assert "token=supersecret" in text
    assert report["plugins"]["plugins"][0]["error"] == "token=supersecret"
    assert "plugin.test" in text


def test_passive_support_report_does_not_call_active_checks(monkeypatch):
    from cifereca.brokers import DiagnosticsBrokerImpl

    invoked = []

    def active_checks(_self):
        invoked.append(True)
        return (SimpleNamespace(
            check_id="health.mock", component="core", status="warning",
            message="simulated", detail={}, scope="core",
        ),)

    monkeypatch.setattr(DiagnosticsBrokerImpl, "active_checks", active_checks)
    monkeypatch.setattr(support_diagnostic, "_release_metadata", lambda: {})
    monkeypatch.setattr(
        support_diagnostic, "_runtime_state", lambda: {"status": "ok"},
    )
    monkeypatch.setattr(
        support_diagnostic, "_database_state", lambda: {"status": "ok"},
    )
    monkeypatch.setattr(
        support_diagnostic, "_config_state", lambda: {"status": "ok"},
    )

    service = Service()
    passive = DiagnosticsBrokerImpl(service).support_report(
        verbose=False, include_checks=False,
    )
    assert invoked == []
    assert passive["checks_executed"] is False
    assert passive["checks"] == []
    assert passive["result"] == "warning"  # plugin degraded, not an active check
    assert passive["plugins"]["attention"] == 1

    ordinary = DiagnosticsBrokerImpl(service).support_report()
    assert invoked == [True]
    assert ordinary["checks_executed"] is True
    assert len(ordinary["checks"]) == 1


def test_support_report_rejects_non_bool_checks_option_before_collection():
    with __import__("pytest").raises(ValueError, match="invalid_support_checks_scope"):
        support_diagnostic.build_support_report(Service(), include_checks="no")


def test_support_report_keeps_raw_active_check_details(monkeypatch):
    from cifereca.brokers import DiagnosticsBrokerImpl

    def active_checks(_self):
        return (SimpleNamespace(
            check_id="raw.loan", component="https://example.org/LOAN/123",
            status="warning", message="login=reader@example.org",
            detail={"password": "visible", "token": "keep",
                    "file_path": "/home/reader/loan.epub"},
            scope="plugin",
        ),)

    monkeypatch.setattr(DiagnosticsBrokerImpl, "active_checks", active_checks)
    monkeypatch.setattr(support_diagnostic, "_release_metadata", lambda: {})
    monkeypatch.setattr(support_diagnostic, "_runtime_state", lambda: {"status": "ok"})
    monkeypatch.setattr(support_diagnostic, "_database_state", lambda: {"status": "ok"})
    monkeypatch.setattr(support_diagnostic, "_config_state", lambda: {"status": "ok"})
    service = Service()
    report = support_diagnostic.build_support_report(service)
    assert report["checks"][0]["component"] == "https://example.org/LOAN/123"
    assert report["checks"][0]["message"] == "login=reader@example.org"
    assert report["checks"][0]["detail"] == {
        "password": "visible", "token": "keep",
        "file_path": "/home/reader/loan.epub",
    }
