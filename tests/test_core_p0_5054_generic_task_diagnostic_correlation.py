"""#5054: worker task references do not depend on provider labels."""
from __future__ import annotations

import re

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics
from cifereca.task_queue import PriorityExecutor


def test_synthetic_delivery_and_session_share_exact_worker_scope(tmp_path):
    diag = Diagnostics(level="detailed")
    executor = PriorityExecutor(max_workers=1)
    try:
        def emit():
            diag.add("SYNTHETIC", "acquisition_delivery", "error",
                     detail={"code": "fixture_rejected"}, level="detailed")
            diag.add("SYNTHETIC", "session_state", "warning",
                     detail={"reason": "fixture_disconnected"}, level="detailed")
            diag.add("SYNTHETIC", "unrelated_operation", "warning",
                     detail={"secret": "PRIVATE"}, level="detailed")
        executor.submit(0, emit).result(timeout=3)
    finally:
        executor.shutdown(wait=True)
    rows = diag.snapshot()
    assert len(rows) == 3
    ref = rows[0]["task_ref"]
    assert re.fullmatch(r"[0-9a-f]{12}", ref)
    assert rows[1]["task_ref"] == ref
    assert "task_ref" not in rows[2]
    report = query_diagnostic_events(
        diag, source="core_events", log_path=tmp_path / "absent")
    assert report["total"] == 3
    assert all("provider_id" not in row for row in report["events"])


def test_legacy_supplier_label_does_not_correlate_unrelated_operations():
    diag = Diagnostics(level="detailed")
    executor = PriorityExecutor(max_workers=1)
    try:
        executor.submit(
            0, lambda: diag.add(
                "NUMILOG", "retired_legacy_operation", "warning",
                detail={"private": "PRIVATE"}, level="detailed",
            ),
        ).result(timeout=3)
    finally:
        executor.shutdown(wait=True)
    record, = diag.snapshot()
    assert "task_ref" not in record
