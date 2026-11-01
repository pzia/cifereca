"""#3913: ZIP v1 producer/reader contract, using only stdlib and synthetic events."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import stat
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

import pytest

from cifereca import journal_zip_contract as contract


def _raw(*, secret="TOKEN_NEVER_REPUBLISH"):
    return {"schema_version": 1, "format": "cifereca-raw-journal-events",
            "events": [{"timestamp": "2026-10-31T12:00:00+00:00",
                        "module": "NUMILOG", "message": secret}]}


def _fixture(path: Path, *, payload=None, manifest_changes=None, extra=()):
    document = _raw() if payload is None else payload
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    manifest = contract.daily_journal_manifest(
        date_local="2026-10-31", timezone="Europe/Paris",
        utc_start="2026-10-30T23:00:00+00:00",
        utc_end_exclusive="2026-10-31T23:00:00+00:00",
        event_count=len(document["events"]), raw_bytes=len(raw),
        sha256_hex=sha256(raw).hexdigest(), coverage="available",
        rotation_gaps=[], active_log_missing=False,
    )
    manifest.update(manifest_changes or {})
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(contract.RAW_MEMBER, raw)
        archive.writestr(contract.MANIFEST_MEMBER, json.dumps(manifest))
        archive.writestr(contract.README_MEMBER, "private credentials, no upload")
        for name, data in extra:
            archive.writestr(name, data)
    return document


def test_v1_constant_manifest_and_reader_preserve_exact_raw_evidence(tmp_path):
    path = tmp_path / "actual-structure.zip"
    document = _fixture(path)
    assert contract.JOURNAL_ZIP_SCHEMA_VERSION == 1
    assert contract.DAILY_MEMBERS == frozenset({
        "cifereca-journal-brut.json", "manifest.json", "README.txt",
    })
    assert contract.read_raw_journal_zip(path) == document
    assert "TOKEN_NEVER_REPUBLISH" in json.dumps(contract.read_raw_journal_zip(path))


def test_raw_reader_legacy_compatibility_is_explicit(tmp_path):
    path = tmp_path / "old.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("journal.json", json.dumps(_raw()))
    assert contract.read_raw_journal_zip(path) == _raw()
    with pytest.raises(contract.JournalZipError, match="unexpected_journal_archive"):
        contract.read_raw_journal_zip(path, allow_legacy=False)


@pytest.mark.parametrize(("changes", "reason"), [
    ({"schema_version": True}, "invalid_journal_manifest"),
    ({"schema_version": 2}, "invalid_journal_manifest"),
    ({"raw_file": "manifest.json"}, "invalid_journal_manifest"),
    ({"raw_bytes": 100_000}, "journal_size_mismatch"),
    ({"sha256": "a" * 64}, "journal_digest_mismatch"),
    ({"event_count": 0}, "journal_event_count_mismatch"),
    ({"event_count": -1}, "invalid_journal_manifest"),
])
def test_fail_closed_manifest_integrity_and_schema(tmp_path, changes, reason):
    path = tmp_path / "invalid.zip"
    _fixture(path, manifest_changes=changes)
    with pytest.raises(contract.JournalZipError, match=reason):
        contract.read_raw_journal_zip(path)


@pytest.mark.parametrize("extra", [
    [("unexpected.txt", "x")],
    [("manifest.json", "{}")],
    [("../evidence.json", "{}")],
    [("nested/other.json", "{}")],
])
def test_archive_member_names_duplicates_and_paths_are_refused(tmp_path, extra):
    path = tmp_path / "unsafe.zip"
    _fixture(path, extra=extra)
    with pytest.raises(contract.JournalZipError, match="unexpected_journal_archive"):
        contract.read_raw_journal_zip(path)


def test_symlink_member_refused_without_extraction(tmp_path, monkeypatch):
    path = tmp_path / "symlink.zip"
    with ZipFile(path, "w") as archive:
        link = ZipInfo(contract.RAW_MEMBER)
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, b"private.json")
    with pytest.raises(contract.JournalZipError, match="unexpected_journal_archive"):
        contract.read_raw_journal_zip(path)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("archive extraction forbidden")
    monkeypatch.setattr(ZipFile, "extract", forbidden)
    monkeypatch.setattr(ZipFile, "extractall", forbidden)
    good = tmp_path / "good.zip"
    _fixture(good)
    assert contract.read_raw_journal_zip(good) == _raw()


def test_limits_and_raw_document_version(tmp_path):
    path = tmp_path / "bounded.zip"
    _fixture(path)
    with pytest.raises(contract.JournalZipError, match="invalid_journal_manifest"):
        contract.read_raw_journal_zip(path, max_json_bytes=8)
    with pytest.raises(contract.JournalZipError, match="invalid_journal_manifest"):
        contract.read_raw_journal_zip(path, max_events=0)
    with pytest.raises(contract.JournalZipError, match="journal_limits_invalid"):
        contract.read_raw_journal_zip(path, max_events=True)
    bad = tmp_path / "schema2.zip"
    _fixture(bad, payload={**_raw(), "schema_version": 2})
    with pytest.raises(contract.JournalZipError, match="unsupported_journal_format"):
        contract.read_raw_journal_zip(bad)


def test_unknown_archive_does_not_select_arbitrary_json(tmp_path):
    path = tmp_path / "different-product.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("sync-result.json", "{}")
    with pytest.raises(contract.JournalZipError, match="unexpected_journal_archive"):
        contract.read_raw_journal_zip(path)


def test_excessively_nested_json_is_closed_not_traceback(tmp_path):
    # A tiny ZIP can contain a JSON nesting bomb: the standalone command
    # must emit a stable closed error, not an unhandled RecursionError.
    path = tmp_path / "nested-legacy.zip"
    nested = b"[" * 2500 + b"0" + b"]" * 2500
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("journal.json", nested)
    with pytest.raises(contract.JournalZipError, match="unreadable_journal"):
        contract.read_raw_journal_zip(path)
