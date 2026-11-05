"""#3252: the CLI must use coverage from validated manifest.json, not raw events.

Create only synthetic private-data stand-ins. Never upload or echo ZIP bytes.
"""
from __future__ import annotations

from hashlib import sha256
import json
from zipfile import ZipFile

import pytest

from cifereca.core.kobo_final_verdict_summary import main
from cifereca.journal_zip_contract import (
    MANIFEST_MEMBER, RAW_MEMBER, README_MEMBER,
    daily_journal_manifest, read_raw_journal_zip,
)


_SECRET = "PRIVATE_ROTATION_NAME_MUST_NEVER_LEAK"
_RAW = {
    "format": "cifereca-raw-journal-events", "schema_version": 1,
    "events": [{"message": "OTHER PRIVATE " + _SECRET}],
    # Deliberately wrong: raw event payload must NOT attest ZIP coverage.
    "coverage": "available", "rotation_gaps": [],
    "active_log_missing": False,
}


def _zip(path, *, manifest=True, coverage="partial", gaps=None,
         active_log_missing=False, corrupt_digest=False):
    payload = json.dumps(_RAW, ensure_ascii=False).encode("utf-8")
    with ZipFile(path, "w") as archive:
        if not manifest:
            archive.writestr("journal.json", payload)
            return
        manifest_doc = daily_journal_manifest(
            date_local="2026-11-05", timezone="Europe/Paris",
            utc_start="2026-11-04T23:00:00+00:00",
            utc_end_exclusive="2026-11-05T23:00:00+00:00",
            event_count=1, raw_bytes=len(payload),
            sha256_hex=("f" * 64 if corrupt_digest
                        else sha256(payload).hexdigest()),
            coverage=coverage, rotation_gaps=([] if gaps is None else gaps),
            active_log_missing=active_log_missing,
        )
        archive.writestr(RAW_MEMBER, payload)
        archive.writestr(MANIFEST_MEMBER, json.dumps(manifest_doc))
        archive.writestr(README_MEMBER, "private, do not upload")


@pytest.mark.parametrize(("coverage", "gaps", "missing", "expected"), [
    ("partial", [_SECRET], False, "partial"),
    ("available", [_SECRET], False, "partial"),
    ("available", [], True, "partial"),
    ("available", [], False, "available"),
    ("empty", [], False, "empty"),
])
def test_daily_zip_cli_binds_manifest_coverage_to_verified_archive(
    tmp_path, capsys, coverage, gaps, missing, expected,
):
    path = tmp_path / "PRIVATE_INPUT.zip"
    _zip(path, coverage=coverage, gaps=gaps, active_log_missing=missing)
    assert read_raw_journal_zip(path) == _RAW
    doc, marker = read_raw_journal_zip(path, include_manifest_coverage=True)
    assert doc == _RAW
    assert marker == {
        "coverage": coverage,
        "rotation_gaps_present": bool(gaps),
        "active_log_missing": missing,
    }
    assert _SECRET not in str(marker)
    assert main([str(path)]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["journal_coverage"] == expected
    assert report["status"] == (
        "no_evidence_partial_coverage" if expected == "partial"
        else "no_instrumented_evidence"
    )
    assert report["historical_completeness_attested"] is False
    assert report["occurrences"] == 0
    assert _SECRET not in output
    assert "PRIVATE_INPUT" not in output


def test_single_json_legacy_zip_cannot_self_attest_any_coverage(tmp_path, capsys):
    path = tmp_path / "PRIVATE_LEGACY.zip"
    _zip(path, manifest=False)
    assert read_raw_journal_zip(path) == _RAW
    doc, marker = read_raw_journal_zip(path, include_manifest_coverage=True)
    assert doc == _RAW and marker is None
    assert main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["journal_coverage"] == "unknown"
    assert report["status"] == "no_instrumented_evidence"
    assert report["historical_completeness_attested"] is False


def test_bad_manifest_hash_is_closed_and_never_emits_raw_or_path(tmp_path, capsys):
    path = tmp_path / "PRIVATE_BAD.zip"
    _zip(path, gaps=[_SECRET], corrupt_digest=True)
    assert main([str(path)]) == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {"status": "journal_unreadable"}
    assert _SECRET not in output
    assert "PRIVATE_BAD" not in output


def test_malformed_manifest_coverage_is_unknown_not_available(tmp_path, capsys):
    path = tmp_path / "PRIVATE_BAD_METADATA.zip"
    _zip(path, coverage="available", gaps="not-a-list")
    assert main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["journal_coverage"] == "unknown"
    assert report["historical_completeness_attested"] is False
