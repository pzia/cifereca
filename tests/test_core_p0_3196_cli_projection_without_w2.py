"""#3196: CLI historical reports never import the retired Kobo W2 engine."""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.core import legacy_diagnostic_projection as projection


def test_retired_w2_does_not_reexport_pure_projection_functions():
    from cifereca.core import kobo_backup
    assert not hasattr(kobo_backup, 'safe_kobo_backup_report')
    assert not hasattr(kobo_backup, '_safe_kobo_backup_diagnostic_detail')
    assert projection.safe_kobo_backup_report is not None


def test_cli_redaction_imports_pure_projector_not_w2_engine():
    source = (Path(__file__).resolve().parents[1] / "src/cifereca/cli.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        names = {alias.name for alias in node.names}
        if names & {"safe_kobo_backup_report", "_safe_kobo_backup_diagnostic_detail"}:
            assert node.module == "core.legacy_diagnostic_projection"


def test_pure_redactors_only_allowlist_historical_counters():
    private = "Bearer SECRET_PRIVATE from /home/alice/Book.epub"
    raw = {
        "items": 3,
        "statuses": {"error": 1, private: 4},
        "sources": [{"status": private, "source_id": private,
                     "downloaded": 2, "account": private}],
        "opening_decision": {"schema_version": 1, "status": private,
                             "credential": private},
    }
    result = projection.safe_kobo_backup_report(raw)
    assert result["items"] == 3
    assert result["sources"][0] == {
        "source_index": 1, "status": "unknown", "downloaded": 2
    }
    assert result["opening_decision"] == {
        "schema_version": 1, "status": "unknown", "persisted_enabled": False
    }
    assert private not in repr(result)

    detail = projection._safe_kobo_backup_diagnostic_detail({
        "status": private,
        "attempted": 4,
        "errors": 1,
        "error_summary": [{"code": private, "phase": private, "message": private}],
        "sources": [{"status": private, "attempted": 2, "account": private}],
    })
    assert detail["status"] == "unknown"
    assert detail["attempted"] == 4
    assert detail["error_summary"] == [{"phase": "other", "code": "other"}]
    assert detail["sources"] == [{
        "source_index": 1, "status": "unknown", "attempted": 2
    }]
    assert private not in repr(detail)
