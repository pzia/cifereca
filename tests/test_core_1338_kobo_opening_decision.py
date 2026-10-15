"""P0 #1338: passive Kobo opening-sync decisions contain no identifiers."""
from __future__ import annotations

import json

import pytest

from cifereca.core.sync_coordination import _kobo_opening_decision
from tests.test_kobo_sync_modes import _service, _neutralize_unrelated_sync_work, _enable_on_start


@pytest.mark.parametrize(
    "enabled,connected,capable,source_enabled,due,expected",
    [
        (False, True, True, True, True, "disabled"),
        (True, False, True, True, True, "no_session"),
        (True, True, False, True, True, "capability_unavailable"),
        (True, True, True, False, True, "source_disabled"),
        (True, True, True, True, False, "manual_only"),
        (True, True, True, True, True, "manual_only"),
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
        # Deep scans must never authorize previously manual-only downloads.
        if expected == "manual_only":
            assert _kobo_opening_decision(service, deep=True)["status"] == "manual_only"
    finally:
        service.close()


def test_last_kobo_success_is_projected_as_bounded_age_only(tmp_path, monkeypatch):
    import time
    service = _service(tmp_path)
    try:
        now = int(time.time())
        monkeypatch.setattr(service, "sync_source_enabled", lambda _account: True)
        service.storage.metadata_set("kobo_backup_last_success_at", now - 3720)
        result = _kobo_opening_decision(service)
        assert result["last_success_age_minutes"] in (62, 63)
        assert "kobo_backup_last_success_at" not in json.dumps(result)
        service.storage.metadata_set("kobo_backup_last_success_at", "private-path-value")
        assert _kobo_opening_decision(service)["last_success_age_minutes"] is None
        service.storage.metadata_set("kobo_backup_last_success_at", now + 600)
        assert _kobo_opening_decision(service)["last_success_age_minutes"] is None
    finally:
        service.close()


def test_opening_records_one_sanitized_summary_on_partial(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        _enable_on_start(service)
        service.config.set("kobo_backup.enabled", True)
        monkeypatch.setattr(service, "_kobo_backup_due", lambda: True)
        called = []
        def summary(progress=None):
            called.append(True)
            return {"errors": 2, "downloaded": 1, "attempted": 3,
                    "secret": "token-VERY-PRIVATE", "item_id": "sensitive-book"}
        monkeypatch.setattr(
            service, "_sync_kobo_backup_locked",
            lambda **_kwargs: pytest.fail("global batch is forbidden"),
        )
        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda credential_id, progress=None: summary(progress=progress),
        )
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

def test_disabled_kobo_opening_is_durable_with_no_item_rows(tmp_path, monkeypatch):
    """A second offline process still explains items=0, not 'no error'."""
    from cifereca.core.kobo_backup import safe_kobo_backup_report

    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        service.config.set("kobo_backup.enabled", False)
        result = service._opening_sync_locked(deep=False)
        assert result["kobo_decision"]["status"] == "disabled"
        assert service.storage.backup_list(provider_id="kobo") == []
        raw = service.storage.metadata_get("kobo_backup_opening_decision_v1")
        assert json.loads(raw)["status"] == "disabled"
        assert "vault" not in raw.lower()
    finally:
        service.close()

    other = _service(tmp_path)
    try:
        persisted = other.kobo_backup_diagnostic()
        assert persisted["items"] == 0
        assert persisted["opening_decision"]["status"] == "disabled"
        assert persisted["opening_decision"]["persisted_enabled"] is False
        shared = safe_kobo_backup_report(persisted)
        assert shared["items"] == 0
        assert shared["opening_decision"]["status"] == "disabled"
        assert "vault" not in json.dumps(shared).lower()
    finally:
        other.close()


@pytest.mark.parametrize("invalid", [
    "{invalid-json",
    json.dumps({"schema_version": 1, "status": "secret-account-name",
                "persisted_enabled": "true", "connected_kobo": "5",
                "eligible_kobo": -1, "checked_at": True,
                "token": "Bearer SECRET", "signed_url": "https://secret.example/"}),
])
def test_kobo_persisted_decision_never_echoes_untrusted_metadata(tmp_path, invalid):
    from cifereca.core.kobo_backup import safe_kobo_backup_report

    service = _service(tmp_path)
    try:
        service.storage.metadata_set("kobo_backup_opening_decision_v1", invalid)
        report = safe_kobo_backup_report(service.kobo_backup_diagnostic())
        output = json.dumps(report)
        assert "SECRET" not in output
        assert "secret.example" not in output
        assert "secret-account-name" not in output
        assert "Bearer" not in output
        if invalid.startswith("{invalid"):
            assert report["opening_decision"] is None
        else:
            assert report["opening_decision"] == {
                "schema_version": 1, "status": "unknown", "persisted_enabled": False,
            }
    finally:
        service.close()



@pytest.mark.parametrize("malformed_flag", ["false", "true", 1, 0, None])
def test_kobo_autobackup_opt_in_must_be_literal_boolean(tmp_path, monkeypatch, malformed_flag):
    """A malformed flag must never authorize background downloads."""
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        service.config.set("kobo_backup.enabled", malformed_flag)
        assert service.kobo_backup_enabled() is False
        monkeypatch.setattr(service, "_sync_kobo_backup_locked",
                            lambda progress=None: pytest.fail("unconsented download"))
        response = service._opening_sync_locked(deep=False)
        assert response["kobo_decision"]["status"] == "disabled"
        assert response["kobo_decision"]["persisted_enabled"] is False
    finally:
        service.close()


def test_kobo_reported_batch_error_is_persisted_as_error(tmp_path, monkeypatch):
    """Returning status=error must not masquerade as a successful start."""
    from cifereca.core.kobo_backup import safe_kobo_backup_report

    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        _enable_on_start(service)
        service.config.set("kobo_backup.enabled", True)
        monkeypatch.setattr(service, "_kobo_backup_due", lambda: True)
        monkeypatch.setattr(service, "_sync_kobo_backup_account_locked",
                            lambda credential_id, progress=None: {"status": "error", "errors": 0})
        result = service._opening_sync_locked(deep=False)
        assert result["kobo_decision"]["status"] == "error"
        report = safe_kobo_backup_report(service.kobo_backup_diagnostic())
        assert report["opening_decision"]["status"] == "error"
        assert report["items"] == 0
    finally:
        service.close()


