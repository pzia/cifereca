"""#3882 P1: export staging evidence must not claim lost or impossible proof."""
from __future__ import annotations

import json
from types import SimpleNamespace
import zipfile

from cifereca.core import acquisition_audit, sync_report


_ACCOUNT = SimpleNamespace(
    identity_id="PRIVATE_IDENTITY", provider_id="future",
    credential_id="PRIVATE_CREDENTIAL", unique_id="PRIVATE_ACCOUNT",
)


def _audit(staging):
    return {
        "schema_version": 1, "scope": "exact_source", "network": "not_used",
        "mutations": "none", "verify_files": True,
        "rights": [], "receipts": [], "truncated": False,
        "staging_evidence": staging,
    }


def test_valid_indexed_staging_remains_positive_without_origin_claim():
    good = {
        "coverage": "indexed_account_receipts_only",
        "staged_verified": 1, "staged_visible_in_library": 1,
        "observations": [{
            "receipt_ref": "a" * 64,
            "staging_state": "verified_staging_file",
            "staging_sha_verified": True,
            "staged_visible_in_library": True,
            "origin_proven_by_receipt": False,
        }],
    }
    projected = acquisition_audit.safe_acquisition_audit(_audit(good))
    assert projected["staging_evidence"] == good
    assert projected["truncated"] is False


def test_untrusted_staging_rows_cannot_forge_origin_or_hide_partial_coverage(
    tmp_path,
):
    raw = {
        "coverage": "indexed_account_receipts_only",
        "staged_verified": 999, "staged_visible_in_library": 444,
        "observations": [
            None,
            {"receipt_ref": "PRIVATE_RECEIPT", "staging_state": "verified_staging_file",
             "staging_sha_verified": True, "origin_proven_by_receipt": True},
            {"receipt_ref": "b" * 64, "staging_state": "not_attested",
             "staging_sha_verified": True, "staged_visible_in_library": True,
             "origin_proven_by_receipt": True,
             "secret": "PRIVATE_TOKEN"},
            {"receipt_ref": "c" * 64, "staging_state": "verified_staging_file",
             "staging_sha_verified": True, "staged_visible_in_library": True,
             "origin_proven_by_receipt": False,
             "signed_url": "PRIVATE_SIGNED_URL"},
        ],
        "cookie": "PRIVATE_COOKIE",
    }
    input_data = _audit(raw)
    projected = acquisition_audit.safe_acquisition_audit(input_data)
    stage = projected["staging_evidence"]
    assert projected["truncated"] is True
    assert stage["coverage"] == "truncated"
    assert stage["staged_verified"] == stage["staged_visible_in_library"] == 1
    assert len(stage["observations"]) == 2
    assert stage["observations"][0] == {
        "receipt_ref": "b" * 64, "staging_state": "not_attested",
        "staging_sha_verified": False, "staged_visible_in_library": False,
        "origin_proven_by_receipt": False,
    }
    assert stage["observations"][1]["staging_sha_verified"] is True
    assert all(not item["origin_proven_by_receipt"] for item in stage["observations"])

    standalone = tmp_path / "audit.json"
    acquisition_audit.write_acquisition_audit(standalone, input_data)
    assert json.loads(standalone.read_text()) == projected

    archive_path = tmp_path / "report.zip"
    sync_report.write_sync_report(
        archive_path, account=_ACCOUNT, run_ref="d" * 32,
        before={}, after={"resource_evidence": input_data},
        result={"status": "partial"},
        started_at="2026-11-01T14:00:00+00:00", exit_code=2,
    )
    with zipfile.ZipFile(archive_path) as archive:
        zip_audit = json.loads(archive.read("acquisition-audit-after.json"))
        summary = archive.read("summary.txt").decode("ascii")
        zip_bytes = b"".join(archive.read(name) for name in archive.namelist())
    assert zip_audit["resource_evidence"] == projected
    assert "snapshot_after_resources_truncated: true" in summary
    assert "PRIVATE_" not in standalone.read_text()
    assert b"PRIVATE_" not in zip_bytes


def test_missing_indexed_observations_do_not_claim_exhaustive_coverage():
    report = acquisition_audit.safe_acquisition_audit(_audit({
        "coverage": "indexed_account_receipts_only",
        "staged_verified": 1, "staged_visible_in_library": 1,
        "observations": "PRIVATE_COOKIE",
    }))
    assert report["staging_evidence"]["coverage"] == "unavailable"
    assert "observations" not in report["staging_evidence"]
    assert "PRIVATE_" not in json.dumps(report)
