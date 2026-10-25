"""#3133: canonical journal is a raw physical sequence, not two joined feeds."""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

from cifereca.core.diagnostic_journal import query_journal
from cifereca.diagnostics import Diagnostics
from cifereca import logging_utils


def _writer(path, *, max_bytes=1_000_000, backups=3):
    handler = logging_utils.PrivateRotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
    ))
    handler.addFilter(logging_utils.RedactionFilter())
    return handler


def test_rotations_are_searched_before_pagination_and_no_body_is_masked(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "trace")
    log_path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca.journal.test")
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    handler = _writer(log_path, max_bytes=280, backups=3)
    logger.addHandler(handler)
    try:
        for n in range(12):
            logger.warning("BNF ACSM status=403 sequence=%02d password=visible token=untouched", n)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()
    result = query_journal(log_path, search="bnf acsm", limit=2, offset=2)
    assert result["total"] > 2
    assert len(result["events"]) == 2
    assert result["has_more"] is True
    assert all("password=visible token=untouched" in row["message"] for row in result["events"])
    assert len({row["event_id"] for row in query_journal(log_path, limit=100)["events"]}) == query_journal(log_path, limit=100)["total"]
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in tmp_path.glob("cifereca.log*"))


def test_search_is_accent_case_insensitive_across_raw_values(tmp_path):
    p = tmp_path / "cifereca.log"
    p.write_text(
        "2026-10-25 08:00:00,100 ERROR cifereca [MainThread] Réconciliation BNF échec 403\n"
        "2026-10-25 08:00:00,101 INFO cifereca [MainThread] Reader Browser Recorder\n"
        "2026-10-25 08:00:00,102 INFO cifereca [MainThread] import ACSM terminé\n",
        encoding="utf-8",
    )
    assert query_journal(p, search="RECONCILIATION")["total"] == 1
    assert query_journal(p, search="bnf")["total"] == 1
    assert query_journal(p, search="recorder")["total"] == 1
    assert query_journal(p, search="acsm")["total"] == 1
    assert query_journal(p, search="no match")["total"] == 0


def test_identical_records_keep_distinct_ids_and_multiline_stack(tmp_path):
    p = tmp_path / "cifereca.log"
    p.write_text(
        "2026-10-25 08:00:00,100 ERROR cifereca [MainThread] failed\n"
        "Traceback: password=secret\n"
        "2026-10-25 08:00:00,100 ERROR cifereca [MainThread] failed\n",
        encoding="utf-8",
    )
    rows = query_journal(p)["events"]
    assert len(rows) == 2 and rows[0]["event_id"] != rows[1]["event_id"]
    assert any("password=secret" in row["message"] for row in rows)


def test_generic_diagnostic_is_logged_as_structured_event_once(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "trace")
    p = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    handler = _writer(p)
    logger.addHandler(handler)
    try:
        d = Diagnostics(level="trace")
        d.add("LOAN", "import_acsm", "failed", detail={
            "provider_id": "bnf-pret",
            "acsm_url": "https://pret.bnf.fr/LOAN/1?token=real",
            "password": "real", "login": "reader@example.invalid",
        })
        event, = query_journal(p)["events"]
        assert event["operation"] == "import_acsm"
        assert event["detail"]["password"] == "real"
        assert event["detail"]["login"] == "reader@example.invalid"
        assert event["detail"]["acsm_url"].endswith("token=real")
        assert query_journal(p, search="reader@example.invalid")["total"] == 1
        assert query_journal(p, provider_id="bnf-pret")["total"] == 1
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()


def test_search_refuses_invalid_dates_and_does_not_follow_symlinks(tmp_path):
    p = tmp_path / "cifereca.log"
    p.symlink_to(tmp_path / "outside")
    result = query_journal(p)
    assert result["status"] == "partial"
    with pytest.raises(ValueError):
        query_journal(p, since="2026-10-25")


def test_raw_export_preserves_values_without_visual_pseudonyms(tmp_path):
    import json
    from cifereca.core.diagnostic_journal import export_raw_journal

    file = tmp_path / "cifereca.log"
    file.write_text(
        '2026-10-25 08:00:00,100 ERROR cifereca [MainThread] '
        'login=real@example.test password=real token=untouched '
        'https://pret.bnf.fr/LOAN/abc?signature=visible\\n',
        encoding="utf-8",
    )
    payload = json.loads(export_raw_journal(file))
    assert payload["schema_version"] == 1
    assert payload["format"] == "cifereca-raw-journal-events"
    body = payload["events"][0]["message"]
    assert "login=real@example.test" in body
    assert "password=real" in body
    assert "token=untouched" in body
    assert "signature=visible" in body
    assert payload["atomic"] is False


def test_orphan_after_rotation_never_rewrites_previous_event(tmp_path):
    older = tmp_path / "cifereca.log.1"
    current = tmp_path / "cifereca.log"
    older.write_text(
        "2026-10-25 08:00:00,100 ERROR cifereca [MainThread] older failure\n"
        "older traceback belongs only to older file\n",
        encoding="utf-8",
    )
    current.write_text(
        "ORPHAN=must-not-cross-inodes\n"
        "2026-10-25 08:01:00,100 WARNING cifereca [MainThread] newer warning\n"
        "newer traceback belongs only to newer file\n",
        encoding="utf-8",
    )
    result = query_journal(current)
    assert result["status"] == "partial"
    assert result["sources"][0]["reason"] == "rotation_or_read_gap"
    assert result["total"] == 2
    older_event, newer_event = result["events"]
    assert "older traceback" in older_event["message"]
    assert "newer traceback" not in older_event["message"]
    assert "ORPHAN=" not in older_event["message"]
    assert "newer traceback" in newer_event["message"]
    assert "ORPHAN=" not in newer_event["message"]


@pytest.mark.parametrize(("operation", "code", "level"), [
    ("chromium_probe", "chromium_missing", "warning"),
    ("http_capture", "capture_failed", "error"),
    ("chromium_install", "ready", "info"),
])
def test_attested_browser_recorder_event_survives_restart_in_one_journal_line(
    tmp_path, monkeypatch, operation, code, level,
):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "trace")
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    handler = _writer(path)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        diagnostics = Diagnostics(level="trace")
        diagnostics.add_core_browser_recorder_result(operation, code)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()
    journal = query_journal(path, search=code)
    assert journal["total"] == 1
    event, = journal["events"]
    assert event["source"] == "journal"
    assert event["severity"] == level
    assert event["module"] == "PLUGIN"
    assert event["operation"] == operation
    assert event["detail"]["operation"] == operation
    assert event["detail"]["code"] == code
    assert event["detail"]["domain"] == "browser_recorder"


def _canonical_writer(path):
    handler = _writer(path)
    handler.setFormatter(logging_utils.CanonicalEventFormatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
    ))
    return handler


def test_attested_connection_producer_is_one_canonical_record(tmp_path, monkeypatch):
    """One Core fact, one physical occurrence, with legacy callbacks intact."""
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    handler = _canonical_writer(path)
    observed = []

    class Capture(logging.Handler):
        def emit(self, record):
            observed.append(record)

    capture = Capture()
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(capture)
    try:
        diag = Diagnostics(enabled=False)
        diag.add_core_provider_connect_failure("provider.test", duration_ms=157)
    finally:
        logger.removeHandler(handler)
        logger.removeHandler(capture)
        logger.setLevel(old_level)
        handler.close()

    result = query_journal(path, provider_id="provider.test")
    assert result["total"] == 1
    event, = result["events"]
    assert event["module"] == "PLUGIN"
    assert event["operation"] == "provider connect"
    assert event["status"] == "error"
    assert event["duration_ms"] == 157
    assert event["detail"] == {
        "provider_id": "provider.test", "code": "provider_connect_failed",
    }
    assert path.read_text().count("DIAG_JSON ") == 1
    assert len(observed) == 1
    assert "provider connection failure" in observed[0].getMessage()
    assert "DIAG_JSON" not in observed[0].getMessage()
    assert diag.snapshot() == []


def test_bnf_failure_preserves_typed_context_as_single_journal_event(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    handler = _canonical_writer(path)
    logger.addHandler(handler)
    try:
        diag = Diagnostics(enabled=False)
        diag.add_core_bnf_loan_action({
            "schema_version": 1, "provider_id": "bnf-pret",
            "action": "stage_loan_token", "phase": "provider_delivery",
            "status": "failed", "reason_code": "bnf_access_denied",
            "duration_ms": 10, "task_ref": "abcdef123456",
        })
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()

    result = query_journal(path, search="bnf_access_denied")
    assert result["total"] == 1
    event, = result["events"]
    assert event["module"] == "LOAN"
    assert event["operation"] == "bnf_loan_action"
    assert event["duration_ms"] == 10
    assert event["detail"]["provider_id"] == "bnf-pret"
    assert event["detail"]["reason_code"] == "bnf_access_denied"
    assert event["task_ref"] == "abcdef123456"
    assert query_journal(path, provider_id="bnf-pret")["total"] == 1
    assert path.read_text().count("DIAG_JSON ") == 1


def test_attested_sync_failure_retains_fields_before_and_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    handler = _canonical_writer(path)
    logger.addHandler(handler)
    try:
        Diagnostics(enabled=False).add_core_source_sync(
            "provider.test", phase="completed", status="failed",
            code="source_sync_failed", duration_ms=123,
            counts={"loans": 2, "library": 7},
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()

    result = query_journal(path, search="source_sync_failed")
    assert result["total"] == 1
    event, = result["events"]
    assert event["module"] == "SYNC"
    assert event["operation"] == "source sync"
    assert event["duration_ms"] == 123
    assert event["detail"]["provider_id"] == "provider.test"
    assert event["detail"]["duration_ms"] == 123
    assert event["detail"]["loans"] == 2
    assert event["detail"]["library"] == 7
    assert query_journal(path, provider_id="provider.test")["total"] == 1


def test_canonical_formatter_preserves_original_raw_unstructured_and_invalid_extra():
    template = "%(levelname)s %(message)s"
    formatter = logging_utils.CanonicalEventFormatter(template)
    record = logging.LogRecord(
        "cifereca", logging.ERROR, __file__, 1,
        "import ACSM 403 password=visible token=untouched", (), None,
    )
    assert formatter.format(record) == (
        "ERROR import ACSM 403 password=visible token=untouched"
    )
    record._cifereca_journal_payload = {"unsupported": object()}
    assert formatter.format(record) == (
        "ERROR import ACSM 403 password=visible token=untouched"
    )
    del record._cifereca_journal_payload
    record._cifereca_journal_payload = {
        "schema_version": 1, "category": "LOAN",
        "operation": "import_acsm", "status": "failed",
        "message": "import ACSM 403 password=visible token=untouched",
        "detail": {"password": "visible", "token": "untouched"},
    }
    canonical = formatter.format(record)
    assert canonical.startswith("ERROR DIAG_JSON ")
    assert "password" in canonical and "untouched" in canonical
    # The same record can be consumed by legacy handlers unchanged.
    assert record.getMessage() == "import ACSM 403 password=visible token=untouched"


def test_numilog_startup_canonical_record_uses_one_existing_trace(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cifereca.core.numilog_startup_diagnostics import StartupTrace

    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    handler = _canonical_writer(path)
    logger.addHandler(handler)
    try:
        service = SimpleNamespace(
            storage=SimpleNamespace(metadata_get=lambda _key: None),
            diagnostics=Diagnostics(enabled=False),
        )
        trace = StartupTrace(service, "numilog")
        fields = trace.emit("admission", "started", "running")
        assert fields["phase"] == "admission"
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()

    result = query_journal(path, search="startup_materialization")
    assert result["total"] == 1
    event, = result["events"]
    assert event["module"] == "NUMILOG"
    assert event["operation"] == "startup_materialization"
    assert event["detail"]["phase"] == "admission"
    assert event["detail"]["run_ref"] == fields["run_ref"]
    assert path.read_text().count("DIAG_JSON ") == 1


def test_core_formatter_keeps_distinct_concurrent_occurrences(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    handler = _canonical_writer(path)
    logger.addHandler(handler)
    try:
        diagnostics = Diagnostics(enabled=False)

        def publish(index):
            diagnostics.add_core_provider_connect_failure(
                "provider.test", duration_ms=index,
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(publish, range(20)))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()
    rows = query_journal(path, limit=100)["events"]
    assert len(rows) == 20
    assert len({row["event_id"] for row in rows}) == 20
    assert all(row["module"] == "PLUGIN" for row in rows)
    assert path.read_text().count("DIAG_JSON ") == 20


def test_numilog_terminal_failure_retained_in_errors_only_mode(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cifereca.core.numilog_startup_diagnostics import StartupTrace

    monkeypatch.setattr(logging_utils, "_diagnostic_level", "errors")
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    handler = _canonical_writer(path)
    logger.addHandler(handler)
    try:
        service = SimpleNamespace(
            storage=SimpleNamespace(metadata_get=lambda _key: None),
            diagnostics=Diagnostics(level="errors"),
        )
        trace = StartupTrace(service, "numilog")
        trace.emit("admission", "started", "running")
        trace.emit("completed", "manifest_failed", "failed")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()

    result = query_journal(path)
    assert result["total"] == 1
    event, = result["events"]
    assert event["module"] == "NUMILOG"
    assert event["severity"] == "error"
    assert event["status"] == "error"
    assert event["detail"]["code"] == "manifest_failed"


@pytest.mark.parametrize(("status", "expected_severity"), [
    ("blocked", "warning"),
    ("partial", "warning"),
    ("retryable_error", "warning"),
    ("attention_required", "warning"),
    ("cancelled", "warning"),
    ("warning", "warning"),
    ("timeout", "error"),
    ("failed", "error"),
    ("error", "error"),
    ("ok", None),
    ("success", None),
])
def test_generic_non_success_facts_survive_errors_only_journal(
    tmp_path, monkeypatch, status, expected_severity,
):
    """Severity filtering cannot silently lose a Core-reported warning."""
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "errors")
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    previous = logger.level
    handler = _canonical_writer(path)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        collector = Diagnostics(level="errors")
        collector.add(
            "PLUGIN", "loan import", status,
            detail={"code": "provider_result", "token": "keep-original"},
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()

    response = query_journal(path)
    assert response["total"] == (0 if expected_severity is None else 1)
    if expected_severity:
        event, = response["events"]
        assert event["severity"] == expected_severity
        assert event["status"] == status
        assert event["operation"] == "loan import"
        assert event["detail"]["token"] == "keep-original"
