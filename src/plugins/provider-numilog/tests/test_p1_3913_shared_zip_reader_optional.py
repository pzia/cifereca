"""#3913: optional shared v1 reader, without losing the standalone ZIP CLI."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest


MODULE = Path(__file__).resolve().parents[1] / "numilog_delivery_diagnose.py"
spec = importlib.util.spec_from_file_location("numilog_shared_zip_optional", MODULE)
diagnose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnose)


def _journal():
    return {"format": "cifereca-raw-journal-events", "schema_version": 1,
            "events": [{
                "timestamp": "2026-11-01T08:00:00+00:00",
                "module": "NUMILOG", "operation": "acquisition_delivery",
                "detail": {"action": "000000000001",
                           "phase": "completed", "code": "http_error",
                           "source": "owned_card", "secret": "NEVER_SHOW_TOKEN"},
            }]}


def test_shared_reader_is_used_with_closed_error_projection(monkeypatch, tmp_path, capsys):
    real_import = diagnose.importlib.import_module
    calls = []
    class SharedError(ValueError):
        pass
    def shared_read(path, *, max_json_bytes, max_events):
        calls.append((path, max_json_bytes, max_events))
        if path.name == "invalid.zip":
            raise SharedError("journal_digest_mismatch")
        return _journal()

    api = SimpleNamespace(
        JournalZipError=SharedError, read_raw_journal_zip=shared_read,
    )
    def optional_import(name):
        if name == "cifereca.plugin_api.journal_zip":
            return api
        return real_import(name)
    monkeypatch.setattr(diagnose.importlib, "import_module", optional_import)

    path = tmp_path / "shared.zip"  # No file: spy proves no legacy ZIP open.
    assert diagnose.load_journal(path) == _journal()
    assert calls == [(path, diagnose._MAX_ARCHIVE_BYTES, diagnose._MAX_EVENTS)]
    assert diagnose.main([str(path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["counts"] == {"http_refusal_without_hop": 1}
    assert "NEVER_SHOW_TOKEN" not in json.dumps(result)

    with pytest.raises(diagnose.JournalFormatError, match="journal_digest_mismatch"):
        diagnose.load_journal(tmp_path / "invalid.zip")
    assert diagnose.main([str(tmp_path / "invalid.zip")]) == 2
    assert "journal_digest_mismatch" in capsys.readouterr().err


def test_without_core_the_historical_stdlib_reader_still_works(monkeypatch, tmp_path):
    real_import = diagnose.importlib.import_module
    def missing_optional_module(name):
        if name == "cifereca.plugin_api.journal_zip":
            raise ModuleNotFoundError("not installed")
        return real_import(name)
    monkeypatch.setattr(diagnose.importlib, "import_module", missing_optional_module)

    path = tmp_path / "historic.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("journal.json", json.dumps(_journal()))
    assert diagnose.load_journal(path) == _journal()


def test_shared_api_does_not_run_on_an_unrecognized_format(tmp_path, monkeypatch):
    # Absent-Core fallback must reject foreign ZIP instead of accepting
    # an arbitrary JSON member from an unrelated producer.
    real_import = diagnose.importlib.import_module
    def missing_optional_module(name):
        if name == "cifereca.plugin_api.journal_zip":
            raise ImportError("Core not installed")
        return real_import(name)
    monkeypatch.setattr(diagnose.importlib, "import_module", missing_optional_module)
    path = tmp_path / "not-journal.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("sync-result.json", "{}")
    with pytest.raises(diagnose.JournalFormatError,
                       match="unexpected_journal_archive"):
        diagnose.load_journal(path)
