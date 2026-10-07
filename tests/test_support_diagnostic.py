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


def test_support_report_is_redacted_and_checks_schema(tmp_path, monkeypatch):
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
    assert "supersecret" not in text
    assert "<redacted>" in text
    assert "plugin.test" in text
