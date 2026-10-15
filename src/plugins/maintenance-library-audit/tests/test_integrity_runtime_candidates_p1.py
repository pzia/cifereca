"""#1495: cached or external integrity plans must be validated at run time."""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import IntegrityAuditTool
from tests.test_integrity_integration import Control


@pytest.mark.parametrize("bad", [
    None, [], "PRIVATE_PATH", 7,
    {"path": "PRIVATE_PATH", "format": "pdf", "expected_size": True, "expected_sha256": ""},
    {"path": "PRIVATE_PATH", "format": "pdf", "expected_size": -1, "expected_sha256": ""},
    {"path": "PRIVATE_PATH", "format": "pdf", "expected_size": "PRIVATE_TOKEN", "expected_sha256": ""},
    {"path": "PRIVATE_PATH", "format": "pdf", "expected_size": "1" * 200, "expected_sha256": ""},
    {"path": "PRIVATE_PATH", "format": "pdf", "expected_size": 1.5, "expected_sha256": ""},
    {"path": "PRIVATE_PATH", "format": "pdf", "expected_size": 0, "expected_sha256": []},
    {"path": None, "format": "pdf", "expected_size": 0, "expected_sha256": ""},
])
def test_corrupted_cached_candidate_fails_closed_and_continues(tmp_path, bad):
    healthy = tmp_path / "good.pdf"
    healthy.write_bytes(b"%PDF-1.7\n")
    plan = MaintenancePlan(
        tool_id=IntegrityAuditTool.info.id,
        summary="2 fichiers",
        facts={"candidates": (
            bad,
            {"path": str(healthy), "format": "pdf",
             "expected_size": healthy.stat().st_size,
             "expected_sha256": ""},
        )},
    )
    control = Control()
    result = IntegrityAuditTool().run(plan, control)
    assert not result.ok
    assert result.facts["checked"] == 2
    assert result.facts["invalid"] == 1
    assert result.facts["issues"] == {"reference_invalid": 1}
    assert result.warnings == ("Fichier 1/2 : reference_invalid",)
    assert [(e.get("current"), e.get("total")) for e in control.events] == [
        (0, 2), (1, 2), (2, 2),
    ]
    exported = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "events": control.events, "issues": result.facts["issues"],
    })
    assert "PRIVATE" not in exported


def test_restored_decimal_size_is_supported_and_verified(tmp_path):
    healthy = tmp_path / "good.pdf"
    healthy.write_bytes(b"%PDF-1.7\n")
    plan = MaintenancePlan(
        tool_id=IntegrityAuditTool.info.id,
        summary="one file",
        facts={"candidates": ({
            "path": str(healthy), "format": "pdf",
            "expected_size": str(healthy.stat().st_size),
            "expected_sha256": "",
        },)},
    )
    result = IntegrityAuditTool().run(plan, Control())
    assert result.ok
    assert result.facts["invalid"] == 0
    assert result.facts["checked"] == 1
