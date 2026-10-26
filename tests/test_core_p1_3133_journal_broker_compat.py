"""#3133 — one persisted authority for legacy Diagnostics broker consumers."""
from __future__ import annotations

import json
from types import SimpleNamespace

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.core.diagnostic_journal import (
    journal_text_export,
    recent_diagnostics_snapshot,
)


class ForbiddenMemory:
    def snapshot(self):
        raise AssertionError("broker snapshot must never consult the in-memory collector")

    def text(self, *_args, **_kwargs):
        raise AssertionError("broker text export must never consult the in-memory collector")


def physical_event(index, *, detail=None, message="BNF ACSM failure", operation="bnf_loan_action"):
    payload = {
        "schema_version": 1, "category": "LOAN", "operation": operation,
        "status": "failed", "message": message, "detail": detail or {},
        "task_ref": "a7b9e134d012", "duration_ms": 123,
    }
    return (
        f"2026-10-25 13:00:00,{index:03d} ERROR cifereca "
        f"DIAG_JSON {json.dumps(payload, ensure_ascii=False)}\n"
    )


def broker_at(tmp_path, monkeypatch):
    path = tmp_path / "cifereca.log"
    monkeypatch.setattr("cifereca.paths.log_file", lambda: path)
    return path, DiagnosticsBrokerImpl(SimpleNamespace(diagnostics=ForbiddenMemory()))


def test_snapshot_and_text_export_reuse_physical_ids_and_original_fields(tmp_path, monkeypatch):
    path, broker = broker_at(tmp_path, monkeypatch)
    private = {
        "login": "reader@example.invalid", "password": "password-raw",
        "token": "token-raw", "cookie": "cookie-raw",
        "url": "https://example.invalid/loan?signature=not-redacted",
        "path": "/home/reader/books/loan.acsm",
    }
    physical = physical_event(0, detail=private)
    path.write_text(
        physical + physical
        + "2026-10-25 13:00:01,000 INFO cifereca plain unstructured trace\n",
        encoding="utf-8",
    )
    original = broker.events(source="journal", limit=50)
    assert original["total"] == 3
    expected = [e for e in original["events"] if e.get("operation")]
    snapshot = broker.snapshot()
    assert len(snapshot) == 2
    assert [r["event_id"] for r in snapshot] == [r["event_id"] for r in expected]
    assert len({r["event_id"] for r in snapshot}) == 2
    assert all(r["detail"] == private and r["source"] == "journal" for r in snapshot)
    assert all(r["operation"] == "bnf_loan_action" and r["duration_ms"] == 123
               and r["task_ref"] == "a7b9e134d012" for r in snapshot)

    text = broker.export_text()
    assert text.startswith("cifereca v")
    assert text.count('"operation": "bnf_loan_action"') == 2
    assert "plain unstructured trace" in text
    for datum in private.values():
        assert datum in text
    exported = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    assert {e["event_id"] for e in exported} == {
        e["event_id"] for e in original["events"]
    }


def test_snapshot_is_recent_bounded_compatible_and_spans_rotations(tmp_path, monkeypatch):
    path, broker = broker_at(tmp_path, monkeypatch)
    older = tmp_path / "cifereca.log.1"
    older.write_text("".join(physical_event(i) for i in range(110)), encoding="utf-8")
    path.write_text(physical_event(111) + "2026-10-25 13:00:01,000 ERROR cifereca raw\n",
                    encoding="utf-8")
    snapshot = broker.snapshot()
    assert len(snapshot) == 100
    assert len({e["event_id"] for e in snapshot}) == 100
    assert snapshot[-1]["operation"] == "bnf_loan_action"
    assert snapshot[-1]["event_id"] == broker.events(
        source="journal", search="bnf_loan_action", offset=110, limit=1,
    )["events"][0]["event_id"]
    assert len(recent_diagnostics_snapshot(path, limit=1)) == 1


def test_empty_journal_never_falls_back_to_memory(tmp_path, monkeypatch):
    path, broker = broker_at(tmp_path, monkeypatch)
    assert broker.snapshot() == ()
    text = broker.export_text()
    assert "coverage: empty_or_missing" in text
    assert "active_log_missing: true" in text
    assert "\n{" not in text
    path.write_text("", encoding="utf-8")
    assert broker.snapshot() == ()
    assert "active_log_missing: false" in broker.export_text()


def test_broken_rotations_visible_and_exports_are_bounded(tmp_path):
    path = tmp_path / "cifereca.log"
    path.write_text(physical_event(0), encoding="utf-8")
    (tmp_path / "cifereca.log.1").write_bytes(b"x" * 2_000_001)
    report = journal_text_export(path, version="test")
    assert "coverage: partial" in report
    assert "rotation_1_partial" in report
    assert '"source": "journal"' in report
