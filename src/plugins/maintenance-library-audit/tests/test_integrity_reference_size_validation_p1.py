"""#1495: malformed reference sizes cannot abort or falsely pass integrity audits.

Only synthetic brokers and small local PDF fixtures. No external files, writes
to the library, network, acquisition or access to private files outside tmp_path.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.test_integrity_integration import (
    Control, _make_integrity_tool,
)


@pytest.mark.parametrize("invalid", [
    True, False, -1, "PRIVATE_BEARER_SIZE", " 13 ", "1" * 21,
    float("nan"), float("inf"), [], {},
])
def test_bad_size_reference_is_quarantined_without_aborting_other_files(
    tmp_path, invalid,
):
    bad = tmp_path / "PRIVATE_INVALID_REFERENCE.pdf"
    good = tmp_path / "PRIVATE_GOOD_REFERENCE.pdf"
    bad.write_bytes(b"%PDF-1.7\npublic bytes")
    good.write_bytes(b"%PDF-1.7\nmore bytes")
    entries = [
        {"identity_id": "id1", "local": True, "artifact_id": "bad-artifact",
         "local_path": str(bad), "title": "PRIVATE_TITLE_ONE",
         "media_format": "pdf"},
        {"identity_id": "id1", "local": True, "artifact_id": "good-artifact",
         "local_path": str(good), "title": "PRIVATE_TITLE_TWO",
         "media_format": "pdf"},
    ]
    artifact_data = {
        "bad-artifact": SimpleNamespace(
            size_bytes=invalid, sha256="", format="pdf",
        ),
        "good-artifact": SimpleNamespace(
            size_bytes=good.stat().st_size, sha256="", format="pdf",
        ),
    }
    tool, plan = _make_integrity_tool(tmp_path, entries, artifact_data)
    assert len(plan.facts["candidates"]) == 2
    assert plan.facts["read_failures"] == 0
    assert plan.facts["reference_failures"] == 1
    assert plan.facts["candidates"][0]["expected_size"] == 0
    assert plan.facts["candidates"][1]["expected_size"] == good.stat().st_size
    assert all("title" not in candidate for candidate in plan.facts["candidates"])
    assert plan.warnings == (
        "1 taille(s) de référence invalide(s) : contrôle de taille indisponible.",
    )

    control = Control()
    result = tool.run(plan, control)
    assert not result.ok
    assert result.changed == 0
    assert result.facts == {
        "checked": 2, "invalid": 0, "missing": 0,
        "read_failures": 0, "reference_failures": 1, "issues": {},
    }
    assert [(event.get("current"), event.get("total"))
            for event in control.events] == [(0, 2), (1, 2), (2, 2)]
    assert result.warnings == plan.warnings
    public = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "events": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", ".pdf", str(tmp_path), "TITLE_ONE"):
        assert secret not in public


@pytest.mark.parametrize("size_source", [0, None, "13", 13])
def test_valid_unknown_and_legacy_decimal_sizes_remain_compatible(
    tmp_path, size_source,
):
    subject = tmp_path / "PRIVATE_FILE.pdf"
    subject.write_bytes(b"%PDF-1.7\nraw")
    # Unknown size (zero or None) means the physical PDF structure is still
    # checked. A nonzero string size is accepted as a legacy numeric value.
    if size_source in ("13", 13):
        subject.write_bytes(b"%PDF-1.7\nmore")
        assert subject.stat().st_size == 13
    tool, plan = _make_integrity_tool(tmp_path, [{
        "identity_id": "id1", "local": True,
        "artifact_id": "one", "local_path": str(subject),
        "title": "PRIVATE_BOOK_TITLE", "media_format": "pdf",
    }], {
        "one": SimpleNamespace(
            size_bytes=size_source, sha256="", format="pdf",
        ),
    })
    assert plan.facts["reference_failures"] == 0
    assert "title" not in plan.facts["candidates"][0]
    result = tool.run(plan, Control())
    assert result.ok
    assert result.facts["checked"] == 1
    assert result.facts["invalid"] == 0
    assert result.facts["reference_failures"] == 0
