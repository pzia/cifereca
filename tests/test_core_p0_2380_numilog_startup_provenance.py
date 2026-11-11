"""P0 #5054: a retired supplier startup worker cannot reattest pseudo-rights."""
import ast
import json
from pathlib import Path

from cifereca.diagnostics import Diagnostics
from cifereca.core.diagnostic_event_feed import query_diagnostic_events


def test_core_retired_startup_producer_and_view_are_physically_absent():
    root = Path(__file__).resolve().parents[1] / "src/cifereca"
    assert not (root / "core/numilog_startup_diagnostics.py").exists()
    assert not (root / "gui/numilog_materialization_status.py").exists()
    source = (root / "diagnostics.py").read_text(encoding="utf-8")
    assert "def add_core_numilog_startup(" not in source
    ast.parse(source)


def test_generic_numilog_startup_details_remain_masked(tmp_path):
    diag = Diagnostics()
    detail = {
        "phase": "completed", "status": "failed",
        "code": "manifest_failed",
        "private": "https://private.invalid/?auth=NEVER_SHOW",
    }
    diag.add("NUMILOG", "startup_materialization", "error", detail=detail)
    output = query_diagnostic_events(
        diag, source="core_events", log_path=tmp_path / "unread",
    )
    assert output["total"] == 1
    row = output["events"][0]
    assert row["message"] == "[opération masquée]"
    assert "detail" not in row and "provider_id" not in row
    assert "NEVER_SHOW" not in json.dumps(output)


def test_retired_worker_cannot_fabricate_current_download_facts():
    from cifereca.core.diagnostic_numilog_projection import public_numilog_fields
    assert public_numilog_fields(
        "startup_materialization",
        {"status": "complete", "downloaded": 1, "attempted": 1},
    ) == {}
