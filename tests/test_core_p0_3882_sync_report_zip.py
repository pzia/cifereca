"""#3882 ZIP report: atomic, 0600, closed diagnostics, independent of HTTP trace."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
import stat
from zipfile import ZipFile

import pytest

from cifereca.core import sync_report_zip as report


def _audit(ref="a" * 24):
    return {
        "schema_version": 1, "scope_ref": ref, "source_count": 1,
        "receipt_count": 1, "counts": {"staged_unpublished": 1,
                                      "staged_visible_in_library": 1},
        "coverage": {"staging": "configured_roots_only",
                     "filesystem": "sha_checked"},
        "sources": [{
            "source_ref": "b" * 24, "kind": "purchase", "source_state": "active",
            "code": "staged_unpublished", "match_basis": "origin_exact",
            "synchronizable": True, "local_artifact_found": True,
            "origin_link_present": True, "origin_link_account_match": True,
            "published_final_placement": False, "staging_only": True,
            "ui_projection_linked": False, "staged_visible_in_library": True,
            "separate_local_and_remote_cards": True,
            "signed_url": "https://private.test/?token=BAD",
            "title": "DO_NOT_EXPOSE",
        }],
        "receipts": [{
            "receipt_ref": "c" * 24, "state": "complete",
            "artifact_recorded": True, "attempt_count": 1,
            "error_code": "http_error", "raw": "Bearer BAD",
        }],
        "raw_log": "Cookie: BIG_SECRET",
    }


def _write(path, **overrides):
    params = dict(
        provider_id="numilog", run_ref="d" * 24,
        started_at=datetime(2026, 10, 31, 14, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 10, 31, 14, 1, tzinfo=timezone.utc),
        exit_code=2, sync_result={
            "status": "partial", "downloaded": 2, "scanned": 6,
            "raw": "SECRET", "code": "source_sync_partial",
        },
        before=_audit(), after=_audit(),
        events=({
            "operation": "delivery", "status": "failed", "code": "http_error",
            "raw_http": "Authorization: Bearer SECRET",
            "task_ref": "private-task",
        },),
    )
    params.update(overrides)
    return report.write_sync_report(path, **params)


def test_full_zip_contents_closed_fields_exact_build_identity(tmp_path, monkeypatch):
    known = {
        "status": "known", "commit_sha": "e" * 40, "build_version": "1.0",
        "build_timestamp": "2026-10-31T10:00:00+00:00",
        "distribution_mode": "wheel", "origin": "wheel_manifest",
    }
    monkeypatch.setattr(report, "build_identity", lambda: known)
    target = tmp_path / "report.zip"
    _write(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with ZipFile(target) as z:
        assert set(z.namelist()) == {
            "manifest.json", "sync-result.json", "acquisition-audit-before.json",
            "acquisition-audit-after.json", "events.jsonl", "summary.txt",
        }
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["build_identity"]["commit_sha"] == "e" * 40
        assert manifest["status"] == "partial"
        assert manifest["exit_code"] == 2
        assert manifest["http_trace_included"] is False
        assert json.loads(z.read("events.jsonl")) == {
            "operation": "delivery", "status": "failed", "code": "http_error",
        }
    with open(target, "rb") as f:
        data = f.read()
    for secret in (b"BAD", b"BIG_SECRET", b"Authorization", b"Bearer",
                   b"DO_NOT_EXPOSE", b"private-task", b"SECRET"):
        # Compressed bytes alone are not enough: also inspect archive members.
        with ZipFile(target) as z:
            assert all(secret not in z.read(name) for name in z.namelist())


def test_exclusive_create_never_overwrites_file_or_symlink(tmp_path):
    destination = tmp_path / "report.zip"
    destination.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="report_destination_exists"):
        _write(destination)
    assert destination.read_text() == "keep"
    destination.unlink()
    destination.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(FileExistsError, match="report_destination_exists"):
        _write(destination)


def test_foreign_scope_and_opaque_id_refused_without_zip(tmp_path):
    target = tmp_path / "report.zip"
    with pytest.raises(ValueError, match="report_scope_changed"):
        _write(target, after=_audit(ref="f"*24))
    assert not target.exists()
    with pytest.raises(ValueError, match="report_opaque_reference_invalid"):
        _write(target, before=_audit(ref="private credential"))
    assert not target.exists()


def test_closed_event_types_and_limits(tmp_path):
    target = tmp_path / "report.zip"
    with pytest.raises(ValueError, match="report_events_invalid"):
        _write(target, events=({"operation": "download", "status": "failed"},))
    with pytest.raises(ValueError, match="report_events_invalid"):
        _write(target, events=({"operation": "delivery", "status": "bad status"},))
    assert not target.exists()


def test_missing_event_capture_explicitly_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "build_identity", lambda: {
        "status": "unavailable", "commit_sha": None,
    })
    target = tmp_path / "failed.zip"
    _write(target, sync_result={"status": "failed", "reason": "operation_busy"},
           events=())
    with ZipFile(target) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["events_coverage"] == "unavailable"
        assert manifest["status"] == "failed"
        assert z.read("events.jsonl") == b""


def test_output_validation_prevents_partial_file(tmp_path):
    target = tmp_path / "report.zip"
    with pytest.raises(ValueError, match="report_clock_invalid"):
        _write(target, finished_at=datetime(2026, 10, 31))
    assert not target.exists()
    with pytest.raises(ValueError, match="report_request_invalid"):
        _write(tmp_path / "report.json")
    assert not (tmp_path / "report.json").exists()
