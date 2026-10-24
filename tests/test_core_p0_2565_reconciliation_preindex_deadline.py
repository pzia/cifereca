"""P0 #2565: comparison's deadline includes SQLite lookup/index preparation.

No incomplete index may be used to attest a right or infer a missing book.
Observations are synthetic and no SQLite/file/provider mutation occurs.
"""
from __future__ import annotations

from copy import deepcopy
import json

from cifereca.core import library_reconciliation as engine
from cifereca.core.library_reconciliation import EvidenceSnapshot, reconcile
from tests.test_core_p0_2796_reconciliation_repeatability import _fixture


def _large_artifact_index():
    inventory, tables = _fixture(1)
    # Exactly 128 artifact observations trigger the first amortized
    # clock/cancellation checkpoint WHILE indexing, before file comparisons.
    for index in range(127):
        tables["artifacts"].append({
            "artifact_id": f"other-{index}",
            "sha256": f"{index + 1000:064x}",
            "size": 123,
            "media_format": "epub",
        })
    return inventory, tables


def _assert_no_early_provenance(report):
    assert report.files == {}
    assert report.edges == []
    assert report.unverified_rights == [0]
    assert "confirmed_link" not in report.redacted()["counts"]
    public = json.dumps(report.redacted())
    assert "PRIVATE_LIBRARY" not in public
    assert "credential" not in public


def test_elapsed_index_time_counts_against_total_comparison_deadline(monkeypatch):
    inventory, tables = _large_artifact_index()
    original = deepcopy(tables)
    times = iter((0.0, 10.0))
    monkeypatch.setattr(engine.time, "monotonic", lambda: next(times, 10.0))
    result = reconcile(inventory, EvidenceSnapshot(tables), seconds=10)
    assert result.reasons == {"comparison_budget"}
    _assert_no_early_provenance(result)
    assert tables == original


def test_cancel_during_indexing_never_publishes_a_tentative_right():
    inventory, tables = _large_artifact_index()
    checks = []

    def cancel():
        checks.append(1)
        return True

    result = reconcile(inventory, EvidenceSnapshot(tables), cancelled=cancel)
    assert checks
    assert result.reasons == {"cancelled"}
    _assert_no_early_provenance(result)


def test_small_index_preserves_exact_attested_links_without_new_reason():
    inventory, tables = _fixture(2)
    result = reconcile(inventory, EvidenceSnapshot(tables), seconds=10)
    assert result.reasons == set()
    assert result.redacted()["counts"]["confirmed_link"] == 2
    assert result.unverified_rights == []
    assert sum(edge.certainty == "attested" for edge in result.edges) == 2
