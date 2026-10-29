"""#2888: physical canonical journal's real worker ref reaches Diagnostics.

Unlike legacy mock-row tests, these use the actual journal parser and the
existing UI projection. No account, provider request or fabricated inference.
"""
from __future__ import annotations

import json

from cifereca.core.diagnostic_journal import query_journal
from diagnostics_explorer import sanitized_rows, linked_operation_groups


def line(level, value):
    return f"2026-10-29 07:00:00,123 {level} cifereca [Worker] {value}\n"


def test_exact_canonical_worker_terminal_groups_with_source_sync(tmp_path):
    ref = "abcdef012345"
    event = {
        "schema_version": 1, "category": "SYNC", "operation": "source_sync",
        "status": "failed", "message": "Source synchronization blocked",
        "task_ref": ref, "detail": {
            "provider_id": "numilog", "phase": "terminal",
            "code": "operation_busy",
        },
    }
    log = tmp_path / "cifereca.log"
    log.write_text(
        line("ERROR", "DIAG_JSON " + json.dumps(event))
        + line("ERROR", f"TRACE task correlation task_ref={ref} status=failed")
        + line("ERROR", "a different failure at the same millisecond"),
        encoding="utf-8",
    )
    actual = query_journal(log, limit=50)
    rows = sanitized_rows(actual, include_detail=True)
    groups = linked_operation_groups(rows)
    matched = next(g for g in groups if g["task_ref"] == ref)
    assert matched["count"] == 2
    assert {r["status"] for r in matched["events"]} == {"failed"}
    assert {r.get("provider_id") for r in matched["events"]} == {
        "numilog", None,
    }
    assert len([g for g in groups if g["task_ref"] is None]) == 1
    assert actual["total"] == 3
