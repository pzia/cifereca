"""#3913: canonical Core daily ZIP compatibility for offline Numilog diagnosis."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


MODULE = Path(__file__).resolve().parents[1] / "numilog_delivery_diagnose.py"
spec = importlib.util.spec_from_file_location("numilog_delivery_diagnose_p1_3913", MODULE)
diagnose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnose)


def _journal():
    return {
        "format": "cifereca-raw-journal-events", "schema_version": 1,
        "atomic": False, "coverage": "partial", "rotation_gaps": [],
        "events": [
            {"timestamp": "2026-10-31T10:00:00+00:00",
             "module": "NUMILOG", "operation": "acquisition_delivery",
             "task_ref": "test-task",
             "detail": {"action": "000000000001", "source": "owned_card",
                        "phase": "completed", "code": "http_error"}},
            {"timestamp": "2026-10-31T10:01:00+00:00",
             "message": "secret from unrelated event: SHOULD_NOT_LEAK"},
        ],
    }


def _canonical_zip(path, *, document=None, changes=None, extra=None):
    document = _journal() if document is None else document
    # Match Core producer's UTF-8 iterencode byte contract, not JSON display.
    raw = json.dumps(document, ensure_ascii=False, allow_nan=False,
                     separators=(",", ":")).encode("utf-8")
    manifest = {
        "schema_version": 1, "format": "cifereca-journal-daily-zip",
        "source": "journal", "date_local": "2026-10-31", "timezone": "Europe/Paris",
        "utc_start": "2026-10-30T22:00:00+00:00",
        "utc_end_exclusive": "2026-10-31T23:00:00+00:00",
        "event_count": len(document["events"]),
        "raw_file": "cifereca-journal-brut.json",
        "raw_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "atomic": False, "filter": "local_day_only", "ui_filters_applied": False,
    }
    if changes:
        manifest.update(changes)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cifereca-journal-brut.json", raw)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("README.txt", "Export brut privé : secret=README_SHOULD_NOT_LEAK\n")
        for name, value in extra or []:
            archive.writestr(name, value)
    return raw


def test_daily_zip_selected_by_manifest_not_archive_member_count(tmp_path, capsys):
    path = tmp_path / "export-du-jour.zip"
    _canonical_zip(path)
    assert diagnose.load_journal(path) == _journal()
    assert diagnose.main([str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["counts"] == {"http_refusal_without_hop": 1}
    assert report["coverage"]["journal_events"] == 2
    assert "SHOULD_NOT_LEAK" not in json.dumps(report)
    assert "README_SHOULD_NOT_LEAK" not in json.dumps(report)


def test_legacy_single_member_zip_remains_supported(tmp_path):
    path = tmp_path / "legacy.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("journal.json", json.dumps(_journal()))
    assert diagnose.load_journal(path) == _journal()


@pytest.mark.parametrize(("changes", "reason"), [
    ({"format": "foreign-format"}, "invalid_journal_manifest"),
    ({"schema_version": True}, "invalid_journal_manifest"),
    ({"raw_file": "manifest.json"}, "invalid_journal_manifest"),
    ({"sha256": "0" * 64}, "journal_digest_mismatch"),
    ({"raw_bytes": 9999}, "journal_size_mismatch"),
    ({"event_count": 1}, "journal_event_count_mismatch"),
    ({"event_count": -1}, "invalid_journal_manifest"),
])
def test_manifest_is_validated_before_trusting_an_export(tmp_path, changes, reason):
    path = tmp_path / "forged.zip"
    _canonical_zip(path, changes=changes)
    with pytest.raises(diagnose.JournalFormatError, match=reason):
        diagnose.load_journal(path)


@pytest.mark.parametrize("extra", [
    [("unexpected.txt", "no")],
    [("manifest.json", "{}")],  # Duplicate entry cannot silently override.
    [("../traversal.txt", "no")],
    [("dir/evil.json", "{}")],
])
def test_reject_extra_duplicate_or_path_member(tmp_path, extra):
    path = tmp_path / "invalid.zip"
    _canonical_zip(path, extra=extra)
    with pytest.raises(diagnose.JournalFormatError, match="unexpected_journal_archive"):
        diagnose.load_journal(path)


def test_manifest_and_readme_are_bounded_before_decompression(tmp_path):
    # Exercise the *actual* v1 limits in both the shared Core reader and the
    # standalone fallback. Patching a legacy-only constant no longer tests
    # the active reader when cifereca.plugin_api.journal_zip is installed.
    path = tmp_path / "oversize-manifest.zip"
    _canonical_zip(path, changes={"padding": "x" * (70 * 1024)})
    with pytest.raises(diagnose.JournalFormatError, match="journal_too_large"):
        diagnose.load_journal(path)

    source = tmp_path / "normal.zip"
    _canonical_zip(source)
    with zipfile.ZipFile(source) as archive:
        raw = archive.read("cifereca-journal-brut.json")
        manifest = archive.read("manifest.json")
    readme = tmp_path / "oversize-readme.zip"
    with zipfile.ZipFile(readme, "w") as archive:
        archive.writestr("cifereca-journal-brut.json", raw)
        archive.writestr("manifest.json", manifest)
        archive.writestr("README.txt", b"X" * (17 * 1024))
    with pytest.raises(diagnose.JournalFormatError, match="journal_too_large"):
        diagnose.load_journal(readme)


def test_legacy_reader_private_manifest_limit_remains_enforced(tmp_path, monkeypatch):
    path = tmp_path / "legacy-manifest-bound.zip"
    _canonical_zip(path)
    # A standalone Numilog diagnostic also runs when Core is unavailable;
    # retain the original regression for that independent parser.
    monkeypatch.setattr(diagnose, "_MAX_MANIFEST_BYTES", 10)
    monkeypatch.setattr(diagnose, "_shared_journal_reader", lambda _path: None)
    with pytest.raises(diagnose.JournalFormatError, match="journal_too_large"):
        diagnose.load_journal(path)


def test_raw_json_is_bounded_before_load(tmp_path, monkeypatch):
    path = tmp_path / "oversize-raw.zip"
    _canonical_zip(path)
    monkeypatch.setattr(diagnose, "_MAX_ARCHIVE_BYTES", 128)
    with pytest.raises(diagnose.JournalFormatError, match="invalid_journal_manifest"):
        diagnose.load_journal(path)


def test_unreadable_daily_json_does_not_fallback_to_manifest_json(tmp_path):
    path = tmp_path / "wrong.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("cifereca-journal-brut.json", "{not-json")
        archive.writestr("manifest.json", "{}")
        archive.writestr("README.txt", "README")
    with pytest.raises(diagnose.JournalFormatError, match="invalid_journal_manifest"):
        diagnose.load_journal(path)


def test_never_extracts_archive_members(tmp_path, monkeypatch):
    path = tmp_path / "safe.zip"
    _canonical_zip(path)
    def forbidden_extract(*args, **kwargs):
        raise AssertionError("untrusted ZIP must never be extracted")
    monkeypatch.setattr(zipfile.ZipFile, "extract", forbidden_extract)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", forbidden_extract)
    assert diagnose.load_journal(path)["events"] == _journal()["events"]
