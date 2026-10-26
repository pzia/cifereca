"""#3133: journal rotation continuity must not overstate retained coverage.

All log files are synthetic. No network, plugins, credential or GTK activity.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_journal import (
    export_raw_journal, journal_text_export, query_journal,
)


def _line(seq: int, message: str) -> str:
    return (
        f"2026-10-26 09:00:{seq:02d},123 ERROR cifereca "
        f"[MainThread] {message}\n"
    )


def _put(tmp_path, rotation: int, text: str) -> None:
    name = "cifereca.log" + (f".{rotation}" if rotation else "")
    (tmp_path / name).write_text(text, encoding="utf-8")


@pytest.mark.parametrize("present,missing", [
    ((0, 2), ("rotation_1_missing",)),
    ((0, 1, 3), ("rotation_2_missing",)),
    ((0, 3), ("rotation_2_missing", "rotation_1_missing")),
    ((1, 3), ("rotation_2_missing",)),
])
def test_missing_middle_rotation_is_partial_in_query_and_exports(
    tmp_path, present, missing,
):
    for n in present:
        _put(tmp_path, n, _line(n, f"BNF ACSM attempt rotation={n}"))
    log = tmp_path / "cifereca.log"
    result = query_journal(log, search="ACSM", limit=1, offset=1)
    assert result["status"] == "partial"
    assert result["sources"][0]["status"] == "partial"
    assert result["sources"][0]["reason"] == "rotation_or_read_gap"
    assert result["total"] == len(present)
    assert len(result["events"]) == 1
    assert result["events"][0]["source"] == "journal"
    document = json.loads(export_raw_journal(log))
    assert document["coverage"] == "partial"
    assert set(missing).issubset(set(document["rotation_gaps"]))
    assert len(document["events"]) == len(present)
    text = journal_text_export(log, version="test")
    for hole in missing:
        assert hole in text
    assert len({row["event_id"] for row in document["events"]}) == len(present)


@pytest.mark.parametrize("present", [
    (0,), (0, 1), (0, 1, 2), (0, 1, 2, 3),
])
def test_initial_missing_older_rotations_are_normal(tmp_path, present):
    for n in present:
        _put(tmp_path, n, _line(n, "known recent event"))
    result = query_journal(tmp_path / "cifereca.log")
    assert result["status"] == "available"
    assert result["sources"][0]["reason"] == ""
    assert result["total"] == len(present)
    document = json.loads(export_raw_journal(tmp_path / "cifereca.log"))
    assert document["coverage"] == "available"
    assert document["rotation_gaps"] == []


def test_missing_active_file_reports_separate_reason(tmp_path):
    _put(tmp_path, 1, _line(1, "older event"))
    result = query_journal(tmp_path / "cifereca.log")
    assert result["status"] == "partial"
    assert result["sources"][0]["reason"] == "active_log_missing"
    document = json.loads(export_raw_journal(tmp_path / "cifereca.log"))
    assert document["active_log_missing"] is True
    # There is no backup hole between two present files.
    assert document["rotation_gaps"] == []


def test_unrelated_text_search_still_excludes_nonmatching_rotations(tmp_path):
    _put(tmp_path, 3, _line(3, "Browser Recorder chromium_missing"))
    _put(tmp_path, 1, _line(1, "BNF ACSM HTTP 403"))
    _put(tmp_path, 0, _line(0, "unrelated message"))
    result = query_journal(tmp_path / "cifereca.log", search="BNF", limit=1)
    assert result["status"] == "partial"
    assert result["total"] == 1
    assert result["events"][0]["message"] == "BNF ACSM HTTP 403"
