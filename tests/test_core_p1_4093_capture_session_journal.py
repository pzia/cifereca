"""P1 #4093: raw capture journal is bounded to one actual process/session.

No prior-day rotation, daily log, external HTTP or GTK is consulted.
All event content is synthetic; raw secrets deliberately remain in RAM.
"""
from __future__ import annotations

import logging
import os

import pytest

from cifereca.core.capture_session_journal import CaptureSessionJournal


@pytest.fixture
def canonical_logger():
    logger = logging.getLogger("cifereca")
    before_level = logger.level
    logger.setLevel(logging.DEBUG)
    yield logger
    logger.setLevel(before_level)


def test_session_contains_only_events_between_attach_and_finish(canonical_logger):
    canonical_logger.error("PRIVATE_BEFORE_SAME_DAY")
    session = CaptureSessionJournal()
    session.start(canonical_logger)
    canonical_logger.error("PRIVATE_STARTUP")
    canonical_logger.error("PRIVATE_PROVIDER_ERROR")
    canonical_logger.error("PRIVATE_SHUTDOWN")
    session.finish()
    canonical_logger.error("PRIVATE_AFTER_SAME_DAY")
    out = session.snapshot()
    raw = out["raw"].decode("utf-8")
    assert out["coverage"] == "complete"
    assert out["record_count"] == 3
    assert out["omitted_records"] == 0
    assert out["process_id"] == os.getpid()
    assert len(out["session_ref"]) == 32
    assert out["started_at_utc"] <= out["finished_at_utc"]
    assert "PRIVATE_STARTUP" in raw and "PRIVATE_SHUTDOWN" in raw
    assert "PRIVATE_PROVIDER_ERROR" in raw
    assert "PRIVATE_BEFORE_SAME_DAY" not in raw
    assert "PRIVATE_AFTER_SAME_DAY" not in raw
    session.finish()
    assert session.snapshot() == out  # closing must be idempotent


def test_overlapping_sessions_still_have_separate_physical_capture_windows(
    canonical_logger,
):
    first = CaptureSessionJournal()
    second = CaptureSessionJournal()
    first.start(canonical_logger)
    canonical_logger.error("PRIVATE_ONLY_FIRST")
    second.start(canonical_logger)
    canonical_logger.error("PRIVATE_BOTH")
    first.finish()
    canonical_logger.error("PRIVATE_ONLY_SECOND")
    second.finish()
    a = first.snapshot()["raw"].decode("utf-8")
    b = second.snapshot()["raw"].decode("utf-8")
    assert "PRIVATE_ONLY_FIRST" in a and "PRIVATE_ONLY_FIRST" not in b
    assert "PRIVATE_BOTH" in a and "PRIVATE_BOTH" in b
    assert "PRIVATE_ONLY_SECOND" not in a and "PRIVATE_ONLY_SECOND" in b
    assert first.session_ref != second.session_ref


def test_foreign_process_record_and_forked_process_are_ignored(
    canonical_logger, monkeypatch,
):
    session = CaptureSessionJournal()
    session.start(canonical_logger)
    record = canonical_logger.makeRecord(
        "cifereca", logging.ERROR, __file__, 1,
        "PRIVATE_FOREIGN_PROCESS", (), None,
    )
    record.process = os.getpid() + 1
    session._handler.handle(record)
    canonical_logger.error("PRIVATE_LOCAL_PROCESS")
    session.finish()
    assert b"PRIVATE_FOREIGN_PROCESS" not in session.snapshot()["raw"]
    assert b"PRIVATE_LOCAL_PROCESS" in session.snapshot()["raw"]


def test_session_enforces_size_quota_without_claiming_complete(canonical_logger):
    session = CaptureSessionJournal(max_bytes=1)
    session.start(canonical_logger)
    canonical_logger.error("PRIVATE_OVERSIZE")
    session.finish()
    out = session.snapshot()
    assert out["coverage"] == "partial"
    assert out["record_count"] == 0
    assert out["omitted_records"] == 1
    assert out["raw"] == b""


def test_raw_session_preserves_multiline_traceback_and_private_text(
    canonical_logger,
):
    session = CaptureSessionJournal()
    session.start(canonical_logger)
    try:
        raise RuntimeError("SYNTHETIC_PRIVATE_COOKIE")
    except RuntimeError:
        canonical_logger.exception("PRIVATE_FAILURE")
    session.finish()
    body = session.snapshot()["raw"].decode("utf-8")
    assert "PRIVATE_FAILURE" in body
    assert "RuntimeError: SYNTHETIC_PRIVATE_COOKIE" in body
    assert "Traceback (most recent call last)" in body


def test_legacy_numilog_logger_uses_same_session_handler(canonical_logger):
    session = CaptureSessionJournal()
    session.start(canonical_logger)
    logging.getLogger("cifereca_numilog").error("PRIVATE_LEGACY_NUMILOG")
    session.finish()
    assert b"PRIVATE_LEGACY_NUMILOG" in session.snapshot()["raw"]
    assert session._handler not in logging.getLogger("cifereca_numilog").handlers


def test_bad_lifecycle_options_fail_closed(canonical_logger):
    with pytest.raises(ValueError, match="capture_journal_invalid_limit"):
        CaptureSessionJournal(max_bytes=True)
    with pytest.raises(ValueError, match="capture_journal_invalid_limit"):
        CaptureSessionJournal(max_bytes=65 * 1024 * 1024)
    session = CaptureSessionJournal()
    with pytest.raises(ValueError, match="capture_journal_not_started"):
        session.finish()
    with pytest.raises(ValueError, match="capture_journal_invalid_logger"):
        session.start(logging.getLogger("other"))
    session.start(canonical_logger)
    with pytest.raises(ValueError, match="capture_journal_already_started"):
        session.start(canonical_logger)
    with pytest.raises(ValueError, match="capture_journal_not_finished"):
        session.snapshot()
    session.finish()
