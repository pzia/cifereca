"""P0 #1338: passive Kobo opening-sync decisions contain no identifiers."""
from __future__ import annotations

import json

import pytest

from cifereca.core.sync_coordination import _kobo_opening_decision
from tests.test_kobo_sync_modes import _service, _neutralize_unrelated_sync_work


@pytest.mark.parametrize(
    "enabled,connected,capable,source_enabled,due,expected",
    [
        (False, True, True, True, True, "disabled"),
        (True, False, True, True, True, "no_session"),
        (True, True, False, True, True, "capability_unavailable"),
        (True, True, True, False, True, "source_disabled"),
        (True, True, True, True, False, "fresh_cache"),
        (True, True, True, True, True, "started"),
    ],
)
def test_decision_distinguishes_reasons_without_provider_request(
    tmp_path, monkeypatch, enabled, connected, capable, source_enabled, due, expected,
):
    service = _service(tmp_path)
    try:
        session = next(iter(service._sessions.values()))
        session.connected = connected
        session.capabilities.backup = capable
        service.config.set("kobo_backup.enabled", enabled)
        monkeypatch.setattr(service, "sync_source_enabled", lambda _account: source_enabled)
        monkeypatch.setattr(service, "_kobo_backup_due", lambda: due)
        # A decision must never use provider/manifest/backup.
        session.library = lambda: (_ for _ in ()).throw(AssertionError("network"))
        session.backup_manifest = session.library
        result = _kobo_opening_decision(service)
        assert result["status"] == expected
        assert result["persisted_enabled"] is enabled
        assert "credential" not in json.dumps(result).lower()
        assert "account" not in json.dumps(result).lower()
        assert "vault" not in json.dumps(result).lower()
        if expected == "started":
            assert _kobo_opening_decision(service, deep=True)["status"] == "started"
    finally:
        service.close()


def test_opening_records_one_sanitized_summary_on_partial(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        service.config.set("kobo_backup.enabled", True)
        monkeypatch.setattr(service, "_kobo_backup_due", lambda: True)
        called = []
        def summary(progress=None):
            called.append(True)
            return {"errors": 2, "downloaded": 1, "attempted": 3,
                    "secret": "token-VERY-PRIVATE", "item_id": "sensitive-book"}
        monkeypatch.setattr(service, "_sync_kobo_backup_locked", summary)
        result = service._opening_sync_locked(deep=False)
        assert len(called) == 1
        decision = result["kobo_decision"]
        assert decision["status"] == "partial"
        assert decision["persisted_enabled"] is True
        assert decision["downloaded"] == 1 and decision["attempted"] == 3
        assert "secret" not in decision and "item_id" not in decision
        entries = [row for row in service.diagnostics.snapshot()
                   if row["operation"] == "automatic backup decision"]
        assert len(entries) == 1
        assert entries[0]["detail"]["status"] == "partial"
        assert "VERY-PRIVATE" not in json.dumps(entries)
        assert "sensitive-book" not in json.dumps(entries)
    finally:
        service.close()


def test_opening_disabled_still_reports_decision_without_backup(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        service.config.set("kobo_backup.enabled", False)
        monkeypatch.setattr(
            service, "_sync_kobo_backup_locked",
            lambda progress=None: (_ for _ in ()).throw(
                AssertionError("backup must not run without saved opt-in")
            ),
        )
        result = service._opening_sync_locked(deep=False)
        assert result["kobo"] is None
        assert result["kobo_decision"]["status"] == "disabled"
        assert len([row for row in service.diagnostics.snapshot()
                    if row["operation"] == "automatic backup decision"]) == 1
    finally:
        service.close()
