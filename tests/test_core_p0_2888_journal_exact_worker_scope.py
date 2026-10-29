"""#2888: keep existing Core worker outcome scoped in canonical raw journal.

All logs are synthetic. No provider, SQLite or network contact occurs.
"""
from __future__ import annotations

import json

from cifereca.core.diagnostic_journal import query_journal


def _line(level, message, *, logger="cifereca", time="07:00:01,123"):
    return f"2026-10-29 {time} {level} {logger} [Worker] {message}\n"


def _provider(ref, *, code="preflight_failed"):
    body = {
        "schema_version": 1, "category": "NUMILOG",
        "operation": "startup_materialization", "status": "blocked",
        "message": "Materialization blocked", "task_ref": ref,
        "detail": {
            "provider_id": "numilog", "code": code,
            "phase": "preflight", "attempted": 0, "downloaded": 0,
        },
    }
    return _line("ERROR", "DIAG_JSON " + json.dumps(body))


def test_exact_worker_outcome_links_to_attested_business_observation(tmp_path):
    log = tmp_path / "cifereca.log"
    ref = "abcdef012345"
    log.write_text(
        _provider(ref)
        + _line("ERROR", f"TRACE task correlation task_ref={ref} status=failed")
        + _line("INFO", "TRACE task correlation task_ref=feedfeedfeed status=success")
        + _line("WARNING", "TRACE task correlation task_ref=012345abcdef status=cancelled"),
        encoding="utf-8",
    )
    rows = query_journal(log, limit=100)["events"]
    assert [row["task_ref"] for row in rows] == [
        ref, ref, "feedfeedfeed", "012345abcdef",
    ]
    assert [row["status"] for row in rows] == [
        "blocked", "failed", "success", "cancelled",
    ]
    assert rows[0]["provider_id"] == "numilog"
    assert all("provider_id" not in row for row in rows[1:])
    assert query_journal(log, search=ref)["total"] == 2
    assert len({row["event_id"] for row in rows}) == 4


def test_unattested_text_and_wrong_severity_never_claim_worker_reference(tmp_path):
    log = tmp_path / "cifereca.log"
    valid = "TRACE task correlation task_ref=abcdef012345 status=failed"
    lines = [
        _line("ERROR", valid, logger="cifereca.provider"),
        _line("INFO", valid),  # conflict: failed must be ERROR
        _line("WARNING", valid),  # conflict
        _line("ERROR", valid + " token=SYNTHETIC"),
        _line("ERROR", "prefix " + valid),
        _line("ERROR", valid + "\\n" + "Traceback"),
        _line("ERROR", "TRACE task correlation task_ref=abcdef012345 status=failed extra"),
        _line("ERROR", "TRACE task correlation task_ref=abcdef01234g status=failed"),
        _line("ERROR", "TRACE task correlation task_ref=000000000000 status=unknown"),
        _line("ERROR", "TRACE task correlation task_ref=abcdef012345 status=failed"),
    ]
    log.write_text("".join(lines), encoding="utf-8")
    rows = query_journal(log, limit=100)["events"]
    assert len(rows) == len(lines)
    assert sum("task_ref" in r for r in rows) == 1
    assert rows[-1]["task_ref"] == "abcdef012345"
    assert all(r["status"] == "unknown" for r in rows[:-1])
    assert all("provider_id" not in r for r in rows)


def test_rotations_do_not_infer_scope_from_timestamps_or_adjacent_logs(tmp_path):
    path = tmp_path / "cifereca.log"
    (tmp_path / "cifereca.log.1").write_text(
        _line("ERROR", "TRACE task correlation task_ref=aaaaaaaaaaaa status=failed"),
        encoding="utf-8",
    )
    path.write_text(
        _line("ERROR", "TRACE task correlation task_ref=bbbbbbbbbbbb status=failed")
        + _line("ERROR", "Other worker failed near same clock"),
        encoding="utf-8",
    )
    events = query_journal(path, limit=100)["events"]
    assert events[0]["task_ref"] == "aaaaaaaaaaaa"
    assert events[1]["task_ref"] == "bbbbbbbbbbbb"
    assert "task_ref" not in events[2] and events[2]["status"] == "unknown"
