"""P1 #3882: unexpected audit fields must never escape through shareable ZIPs."""
from __future__ import annotations

import json
from types import SimpleNamespace
import zipfile

from cifereca.core.sync_report import write_sync_report


_ACCOUNT = SimpleNamespace(
    identity_id="PRIVATE_PROFILE", provider_id="unknown-provider",
    credential_id="PRIVATE_CREDENTIAL", unique_id="PRIVATE_ACCOUNT",
)


def _write(target, before, after):
    write_sync_report(
        target, account=_ACCOUNT, run_ref="f" * 32,
        before=before, after=after, result={"status": "partial", "errors": 1},
        started_at="2026-11-01T00:00:00+00:00", exit_code=2,
    )


def test_audit_zip_exports_only_closed_schema_even_with_credentials(tmp_path):
    ref = "a" * 64
    snapshot = {
        "schema_version": 1, "audit_scope": "exact_source",
        "resource_evidence": {
            "schema_version": 1, "scope": "exact_source", "network": "not_used",
            "mutations": "none", "truncated": False, "verify_files": True,
            "rights": [{
                "resource_ref": ref, "kind": "purchase",
                "right_state": "active", "classification": "complete_local",
                "origin_link_count": 1, "placement_count": 1,
                "file_verification": "verified_sha256",
                "token": "PRIVATE_SECRET_TOKEN", "title": "PRIVATE_TITLE",
            }],
            "receipts": [{
                "receipt_ref": "b" * 64, "status": "canonical_receipt_staged",
                "classification": "staged_unpublished",
                "raw_exception": "PRIVATE_EXCEPTION",
            }],
            "staging_evidence": {
                "coverage": "indexed_account_receipts_only",
                "staged_verified": 1, "staged_visible_in_library": 1,
                "observations": [{
                    "receipt_ref": "b" * 64,
                    "staging_state": "verified_staging_file",
                    "staging_sha_verified": True,
                    "staged_visible_in_library": True,
                    "origin_proven_by_receipt": False,
                    "cookie": "PRIVATE_COOKIE",
                }],
                "auth": "PRIVATE_AUTH",
            },
            "bearer": "PRIVATE_BEARER",
        },
        "physical_verification": "bounded_sha256_checked",
        "origin_link_count": 1, "placement_reference_count": 1,
        "rights_by_kind": {"purchase": 1, "auth": "PRIVATE_AUTH_COUNT"},
        "extra_path": "/PRIVATE_EXPORT_PATH",
    }
    target = tmp_path / "report.zip"
    _write(target, snapshot, snapshot)
    with zipfile.ZipFile(target) as archive:
        before = json.loads(archive.read("acquisition-audit-before.json"))
        after = json.loads(archive.read("acquisition-audit-after.json"))
        manifest = json.loads(archive.read("manifest.json"))
        data = b"".join(archive.read(name) for name in archive.namelist())
    assert before == after
    assert before["resource_evidence"]["rights"][0]["resource_ref"] == ref
    assert before["resource_evidence"]["rights"][0]["file_verification"] == "verified_sha256"
    assert before["resource_evidence"]["staging_evidence"]["observations"][0][
        "staging_sha_verified"
    ] is True
    assert before["rights_by_kind"] == {"purchase": 1}
    assert before["placement_reference_count"] == 1
    assert manifest["evidence_coverage"] == "canonical_sqlite_graph_with_bounded_sha256"
    for forbidden in (
        "PRIVATE_PROFILE", "PRIVATE_CREDENTIAL", "PRIVATE_ACCOUNT",
        "PRIVATE_SECRET_TOKEN", "PRIVATE_TITLE", "PRIVATE_EXCEPTION",
        "PRIVATE_COOKIE", "PRIVATE_AUTH", "PRIVATE_BEARER", "PRIVATE_EXPORT_PATH",
    ):
        assert forbidden.encode() not in data


def test_invalid_evidence_never_claims_verified_or_exposes_free_text(tmp_path):
    target = tmp_path / "untrusted.zip"
    bogus = {
        "physical_verification": "PRIVATE_FAKE_HASH",
        "resource_evidence": {
            "truncated": False,
            "rights": [
                {"resource_ref": "PRIVATE_REFERENCE", "classification": "PRIVATE_CLASS",
                 "file_verification": "PRIVATE_FILE", "origin_link_count": -100},
            ] * 513,
            "receipts": [{"receipt_ref": "PRIVATE_RECEIPT", "status": "PRIVATE_STATUS"}],
        },
        "ledger_by_status": {"pending": True, "PRIVATE_ACCOUNT": 1},
    }
    _write(target, {}, bogus)
    with zipfile.ZipFile(target) as z:
        after = json.loads(z.read("acquisition-audit-after.json"))
        manifest = json.loads(z.read("manifest.json"))
        data = b"".join(z.read(n) for n in z.namelist())
    rights = after["resource_evidence"]["rights"]
    assert len(rights) == 512
    assert after["resource_evidence"]["truncated"] is True
    assert rights[0]["classification"] == "unclassified"
    assert rights[0]["file_verification"] == "not_requested"
    assert "resource_ref" not in rights[0]
    assert "origin_link_count" not in rights[0]
    assert after["ledger_by_status"] == {}
    assert manifest["evidence_coverage"] == "canonical_sqlite_graph_without_filesystem"
    assert b"PRIVATE_" not in data


def test_unavailable_audit_reason_is_closed(tmp_path):
    target = tmp_path / "failed.zip"
    _write(target, {
        "status": "unavailable", "reason": "PRIVATE_PROVIDER_EXCEPTION",
        "http_response": "PRIVATE_COOKIE",
    }, {"rights_by_kind": {"loan": 2}})
    with zipfile.ZipFile(target) as z:
        before = json.loads(z.read("acquisition-audit-before.json"))
        summary = z.read("summary.txt")
        manifest = json.loads(z.read("manifest.json"))
    assert before == {"status": "unavailable", "reason": "audit_unavailable"}
    assert manifest["evidence_coverage"] == "local_audit_unavailable"
    assert b"PRIVATE" not in summary


def test_malformed_rows_are_partial_evidence_not_false_exhaustivity(tmp_path):
    target = tmp_path / "malformed.zip"
    _write(target, {}, {
        "resource_evidence": {
            "truncated": False,
            "rights": [None, {"classification": "source_unlinked"}],
            "receipts": {"unexpected": "PRIVATE_CREDENTIAL"},
        },
    })
    with zipfile.ZipFile(target) as z:
        after = json.loads(z.read("acquisition-audit-after.json"))
        raw = z.read("acquisition-audit-after.json")
    evidence = after["resource_evidence"]
    assert evidence["truncated"] is True
    assert len(evidence["rights"]) == 1
    assert evidence["rights"][0]["classification"] == "source_unlinked"
    assert "receipts" not in evidence
    assert b"PRIVATE_CREDENTIAL" not in raw
