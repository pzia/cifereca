"""#2734: configurable local comparison limits and honest bibliographic coverage."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cifereca.core.library_inventory import FileObservation, Inventory
from cifereca.core.library_reconciliation import EvidenceSnapshot, reconcile
from cifereca.core.library_metadata_candidates import metadata_candidates
from tests.test_library_audit_broker import scoped


def _local_inventory(n=1):
    return Inventory(files=[
        FileObservation(
            "alice", f"/PRIVATE/{i}.epub", 123, "epub", "a" * 64, "ok",
            {"identifiers": [{"scheme": "ISBN", "value": "9780306406157"}]},
        ) for i in range(n)
    ])


def test_right_comparison_generic_10s_and_scoped_30s_are_distinct():
    from cifereca.core import library_reconciliation as engine

    def run(seconds):
        clock = [0.0]

        def advance():
            clock[0] += 9.9
            return False

        with patch.object(engine.time, "monotonic", lambda: clock[0]):
            result = reconcile(_local_inventory(3), EvidenceSnapshot(),
                               seconds=seconds, cancelled=advance)
        return result

    generic = run(10)
    scoped = run(30)
    assert len(generic.files) == 1
    assert generic.reasons == {"comparison_budget"}
    assert len(scoped.files) == 3
    assert scoped.reasons == set()
    assert not any("confirmed_link" in states for states in scoped.files.values())


@pytest.mark.parametrize("budget", [0, -1, 61, True, float("nan"), float("inf"), "PRIVATE"])
def test_reconcile_rejects_unbounded_or_malformed_clock_before_evidence(budget):
    with pytest.raises(ValueError, match="^invalid_comparison_limits$"):
        reconcile(_local_inventory(), EvidenceSnapshot(), seconds=budget)


class _SlowRow(dict):
    def __init__(self, clock):
        super().__init__(identity_id="alice",
                         bibliographic_identifiers=[("isbn13", "9780306406157")])
        self.clock = clock

    def get(self, key, default=None):
        if key == "bibliographic_identifiers":
            self.clock[0] += 6
        return super().get(key, default)


def test_index_30s_timeout_still_reserves_bibliographic_file_matching():
    from cifereca.core import library_metadata_candidates as engine

    def run(seconds, index_seconds):
        clock = [0.0]
        snapshot = EvidenceSnapshot(
            tables={"catalog_entries": [_SlowRow(clock) for _ in range(8)]}
        )
        with patch.object(engine.time, "monotonic", lambda: clock[0]):
            report = metadata_candidates(
                _local_inventory(), snapshot, seconds=seconds,
                index_seconds=index_seconds,
            )
        return report

    generic = run(10, None)
    assert generic.catalog_rows_examined == 2
    assert generic.files_examined == 0
    assert generic.index_completed is False
    assert generic.match_phase_executed is False
    assert generic.reasons == {"metadata_candidate_time_budget"}

    scoped = run(60, 30)
    assert scoped.catalog_rows_examined == 5
    assert scoped.references_examined == 5
    assert scoped.index_completed is False
    assert scoped.match_phase_executed is True
    assert scoped.files_examined == 1
    assert len(scoped.candidates) == 5
    assert {row.certainty for row in scoped.candidates} == {"metadata_only"}
    assert scoped.reasons == {"metadata_candidate_time_budget"}
    output = json.dumps(scoped.redacted())
    assert "PRIVATE" not in output
    assert "9780306406157" not in output


def test_completed_index_can_use_entire_30s_window_for_matching():
    from cifereca.core import library_metadata_candidates as engine
    clock = [0.0]
    snapshot = EvidenceSnapshot(
        tables={"catalog_entries": [_SlowRow(clock) for _ in range(4)]},
    )
    with patch.object(engine.time, "monotonic", lambda: clock[0]):
        report = metadata_candidates(
            _local_inventory(), snapshot, seconds=60, index_seconds=30,
        )
    assert clock == [24.0]
    assert report.index_completed is True
    assert report.match_phase_executed is True
    assert report.files_examined == 1
    assert len(report.candidates) == 4
    assert not report.reasons


@pytest.mark.parametrize("seconds,index_seconds", [
    (0, None), (61, 30), (60, 60), (60, 0),
    (60, True), (60, float("nan")), (60, "PRIVATE"),
])
def test_candidate_time_reservations_fail_closed(seconds, index_seconds):
    with pytest.raises(ValueError, match="^invalid_candidate_limits$"):
        metadata_candidates(
            _local_inventory(), EvidenceSnapshot(),
            seconds=seconds, index_seconds=index_seconds,
        )


def test_scoped_broker_passes_reserved_budgets_without_changing_generic_api(scoped):
    from cifereca.core.library_audit import audit_library
    from cifereca.core.library_audit_broker import LibraryReconciliationReadBrokerImpl

    service, _, _ = scoped
    with patch("cifereca.core.library_audit_broker.audit_library",
               wraps=audit_library) as spy:
        result = LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    assert result["status"] == "complete"
    assert spy.call_count == 1
    options = spy.call_args.kwargs
    assert options["seconds"] == 600
    assert options["comparison_seconds"] == 30
    assert options["candidate_seconds"] == 60
    assert options["candidate_index_seconds"] == 30
    assert result["metadata_candidates"]["match_phase_executed"] is True


@pytest.mark.parametrize("kw", [
    {"comparison_seconds": True},
    {"comparison_seconds": 61},
    {"candidate_seconds": float("nan")},
    {"candidate_index_seconds": 30, "candidate_seconds": 30},
])
def test_audit_rejects_bad_phase_budget_without_touching_disk(tmp_path, kw):
    from cifereca.core.library_audit import audit_library

    absent = tmp_path / "absent"
    with pytest.raises(ValueError, match="^invalid_library_audit_options$"):
        audit_library(tmp_path / "PRIVATE.sqlite", {"alice": (absent,)}, **kw)
    assert not absent.exists()
