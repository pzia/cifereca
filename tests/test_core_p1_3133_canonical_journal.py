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
