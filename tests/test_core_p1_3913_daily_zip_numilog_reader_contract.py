"""#3913: production Core ZIP producer interoperates with Numilog offline reader.

This is a cross-component *Core export contract* test. The plugin consumer is
loaded without an installed provider or network; no acquisitions occur.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import time
from zipfile import ZipFile

from cifereca.core.diagnostic_journal_zip import export_daily_journal_zip


_READER = (
    Path(__file__).resolve().parents[1]
    / "src/plugins/provider-numilog/numilog_delivery_diagnose.py"
)


def _consumer():
    spec = importlib.util.spec_from_file_location("numilog_zip_contract_reader", _READER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _line(day: str, hour: str, document: dict, *, level="ERROR") -> str:
    message = "DIAG_JSON " + json.dumps(document, separators=(",", ":"))
    return f"{day} {hour},000 {level} cifereca [Worker] {message}\n"


def test_real_core_daily_export_is_consumable_offline(tmp_path, monkeypatch, capsys):
    original = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Europe/Paris")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        log = tmp_path / "cifereca.log"
        action = "000000000001"
        record = {
            "schema_version": 1, "category": "NUMILOG",
            "operation": "acquisition_delivery", "status": "error",
            "task_ref": "synthetic-task",
            "detail": {
                "action": action, "phase": "completed",
                "code": "http_error", "source": "owned_card",
                "secret": "SYNTHETIC_CREDENTIAL_NOT_FOR_REPORT",
            },
        }
        log.write_text(
            _line("2026-10-31", "12:00:00", record)
            + _line("2026-11-01", "12:00:00", record),
            encoding="utf-8",
        )
        output = export_daily_journal_zip(
            log, tmp_path / "journal-du-jour.zip",
            now=datetime(2026, 10, 31, 12, tzinfo=timezone.utc),
            zone_name="Europe/Paris",
        )
        reader = _consumer()
        with ZipFile(output) as archive:
            assert set(archive.namelist()) == {
                "cifereca-journal-brut.json", "manifest.json", "README.txt",
            }
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["format"] == "cifereca-journal-daily-zip"
            assert manifest["event_count"] == 1
        payload = reader.load_journal(output)
        assert len(payload["events"]) == 1
        assert payload["events"][0]["module"] == "NUMILOG"
        assert payload["events"][0]["operation"] == "acquisition_delivery"
        assert payload["events"][0]["detail"]["secret"] == (
            "SYNTHETIC_CREDENTIAL_NOT_FOR_REPORT"
        )
        report = reader.analyze(payload)
        assert report["coverage"]["delivery_attempts"] == 1
        assert report["counts"] == {"http_refusal_without_hop": 1}
        assert report["attempts"][0]["terminal_code"] == "http_error"
        assert "SYNTHETIC_CREDENTIAL_NOT_FOR_REPORT" not in json.dumps(report)

        assert reader.main([str(output), "--json"]) == 0
        output_report = json.loads(capsys.readouterr().out)
        assert output_report["counts"] == {"http_refusal_without_hop": 1}
        assert "SYNTHETIC_CREDENTIAL_NOT_FOR_REPORT" not in json.dumps(output_report)
    finally:
        if original is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original)
        if hasattr(time, "tzset"):
            time.tzset()
