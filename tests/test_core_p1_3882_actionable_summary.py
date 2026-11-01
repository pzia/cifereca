"""#3882 P1: shareable report summary reflects only closed canonical evidence."""
from __future__ import annotations

import json
from types import SimpleNamespace
import zipfile

from cifereca.core import sync_report


_ACCOUNT = SimpleNamespace(
    identity_id="PERSONAL_PROFILE", provider_id="numilog",
    credential_id="PRIVATE_CREDENTIAL", unique_id="PRIVATE_ACCOUNT",
)


def test_report_summary_classifies_bounded_local_evidence(tmp_path, monkeypatch):
    from cifereca import startup_revision
    monkeypatch.setattr(startup_revision, "build_identity", lambda: {
        "schema_version": 1, "status": "known", "commit_sha": "a" * 40,
        "build_version": "test",
    })
    before = {
        "physical_verification": "not_performed",
        "origin_link_count": 1,
        "placement_reference_count": 1,
        "resource_evidence": {"rights": [], "receipts": [], "truncated": False},
    }
    after = {
        "physical_verification": "not_performed",
        "origin_link_count": 2,
        "placement_reference_count": 2,
        "resource_evidence": {
            "truncated": True,
            "rights": [
                {"classification": "source_unlinked", "title": "PRIVATE_TITLE"},
                {"classification": "source_unlinked"},
                {"classification": "missing_file", "path": "/private/book.epub"},
                {"classification": "complete_local"},
                {"classification": "other", "error": "PASSWORD_SECRET"},
            ],
            "receipts": [
                {"classification": "staged_unpublished", "signed_url": "PRIVATE_URL"},
                {"classification": "receipt_recorded"},
            ],
        },
    }
    target = tmp_path / "report.zip"
    sync_report.write_sync_report(
        target, account=_ACCOUNT, run_ref="e" * 32,
        before=before, after=after,
        result={"status": "partial", "code": "source_sync_partial",
                "downloaded": 3, "scanned": 7,
                "message": "PASSWORD_SECRET"},
        started_at="2026-11-01T00:00:00+00:00", exit_code=2,
    )
    with zipfile.ZipFile(target) as archive:
        summary = archive.read("summary.txt").decode("ascii")
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["event_coverage"] == "cli_milestones_only"
        assert "status: partial" in summary
        assert "reason_code: source_sync_partial" in summary
        assert "reported_downloaded: 3" in summary
        assert "reported_scanned: 7" in summary
        assert "snapshot_before_origin_link_count: 1" in summary
        assert "snapshot_after_origin_link_count: 2" in summary
        assert "observed_rights_source_unlinked: 2" in summary
        assert "observed_rights_missing_file: 1" in summary
        assert "observed_receipts_staged_unpublished: 1" in summary
        assert "snapshot_after_resources_truncated: true" in summary
        assert "not per-run causality" in summary
        assert "no HTTP/correlated journal proof" in summary
        for secret in (
            "PRIVATE_TITLE", "/private/book.epub", "PRIVATE_URL",
            "PRIVATE_ACCOUNT", "PRIVATE_CREDENTIAL", "PASSWORD_SECRET",
        ):
            assert secret not in summary


def test_summary_unavailable_audit_never_invents_missing_resources():
    summary = sync_report._report_summary(
        outcome={"status": "failed", "code": "sync_unavailable", "errors": 1},
        exit_code=2,
        before={"status": "unavailable", "reason": "SECRET_PATH"},
        after={"status": "unavailable", "reason": "SECRET_EXCEPTION"},
        evidence_coverage="canonical_sqlite_graph_without_filesystem",
    ).decode("ascii")
    assert "audit_before: unavailable" in summary
    assert "audit_after: unavailable" in summary
    assert "reason_code: sync_unavailable" in summary
    assert "reported_errors: 1" in summary
    assert "observed_rights_" not in summary
    assert "SECRET_" not in summary


def test_summary_rejects_unknown_strings_and_unbounded_counts():
    summary = sync_report._report_summary(
        outcome={"status": "success", "code": "PRIVATE", "downloaded": 1_000_001,
                 "scanned": True, "pending": 0},
        exit_code=0,
        before={},
        after={
            "origin_link_count": "PRIVATE_COUNT",
            "placement_reference_count": True,
            "resource_evidence": {
                "rights": [{"classification": "PRIVATE_CLASS"}],
                "receipts": "PRIVATE_RECEIPT",
            },
        },
        evidence_coverage="canonical_sqlite_graph_without_filesystem",
    ).decode("ascii")
    assert "reported_pending: 0" in summary
    for forbidden in ("PRIVATE", "reported_scanned", "reported_downloaded",
                      "observed_rights", "snapshot_after_origin_link_count"):
        assert forbidden not in summary
