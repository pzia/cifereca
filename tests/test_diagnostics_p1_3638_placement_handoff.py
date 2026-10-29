"""#3638: real Core placement facts survive every Diagnostics surface.

No repair, acquisition, scan or SQLite mutation by Diagnostics.
"""
from __future__ import annotations

import copy
import json

import pytest

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.core.diagnostic_journal import query_journal, journal_text_export
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_reconciliation import (
    from_audit_result, public_fields, public_reconciliation_log_message,
    public_reconciliation_log_fields,
)
from cifereca.core import library_audit_broker
from cifereca.diagnostics import Diagnostics
from tests.test_core_placement_observation_handoff import add_rows
from tests.test_library_audit_broker import scoped
from tests.test_library_reconciliation import filesystem
from tests.test_diagnostics_acquisition_fidelity import journal


def _batch(rows, *, total=None, scope=True, probe="complete", reasons=None):
    count = len(rows)
    total = count if total is None else total
    return {
        "schema": "cifereca.placement.observation/1",
        "observation_ref": "1" * 32,
        "identity_id": "alice", "scope_validated": scope,
        "read_only": True, "repair_authorized": False,
        "acquisition_authorized": False,
        "probe_coverage": probe, "inventory_coverage": "complete",
        "reasons": reasons if reasons is not None else [],
        "total_rows": total, "emitted_rows": count, "truncated": total > count,
        "observations": rows,
    }


def _row(name, state="missing", reason="enoent_observed", **extra):
    return {
        "placement_id": name, "artifact_id": "artifact-one",
        "sqlite_state": "present", "state": state, "reason": reason,
        "observation_source": "core_stored_paths",
        "restoration": "not_evaluated", **extra,
    }


def _report(batch):
    return {
        "status": "complete", "reasons": [],
        "inventory": {"status": "complete", "reasons": []},
        "stored_paths": {"status": "complete", "reasons": [], "counts": {}},
        "placement_observations": batch,
    }


def test_real_scoped_broker_projection_journal_search_and_exports(
    scoped, tmp_path, monkeypatch, journal,
):
    service, database, root = scoped
    add_rows(database, [
        ("missing-a", "artifact-shared", root / "a" / "absent.epub", "alice", "missing"),
        ("present-a", "artifact-shared", root / "a" / "PRIVATE.epub", "alice", "present"),
        ("foreign-b", "artifact-shared", root / "b" / "absent.epub", "bob", "missing"),
    ])
    service.diagnostics = Diagnostics()
    before = filesystem(tmp_path)
    public = library_audit_broker.LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    assert filesystem(tmp_path) == before
    assert "placement_observations" not in public
    detail = service.diagnostics.snapshot()[-1]["detail"]
    batch = detail["placement_observations"]
    assert batch["identity_id"] == "alice"
    assert {row["placement_id"] for row in batch["observations"]} == {
        "missing-a", "present-a",
    }
    assert "foreign-b" not in json.dumps(detail)
    missing = next(row for row in batch["observations"] if row["placement_id"] == "missing-a")
    assert (missing["state"], missing["reason"]) == ("missing", "enoent_observed")
    assert missing["restoration"] == "not_evaluated"
    assert "repair_authorized" in batch and not batch["repair_authorized"]
    assert "acquisition_authorized" in batch and not batch["acquisition_authorized"]

    events = query_journal(journal.path, search="missing-a")["events"]
    assert len(events) == 1
    event = events[0]
    assert event["detail"] == detail
    assert event["severity"] == "info"  # Successful audit, not successful presence.
    broker = DiagnosticsBrokerImpl(type("Context", (), {"diagnostics": service.diagnostics})())
    for source in ("all", "app_log", "core_events"):
        assert broker.events(source=source, search="missing-a")["events"] == [event]
    exported = json.loads(broker.export_raw_journal())
    assert event in exported["events"]
    ndjson = [json.loads(line) for line in journal_text_export(journal.path, version="test").splitlines()
              if line.startswith("{")]
    assert event in ndjson
    assert public_reconciliation_log_fields(public_reconciliation_log_message(detail)) == detail


@pytest.mark.parametrize("state,reason", [
    ("missing", "enoent_observed"),
    ("unavailable", "root_missing"),
    ("unavailable", "permission_denied"),
    ("unverified", "not_examined"),
    ("unverified", "out_of_scope"),
    ("present", "regular_file_observed"),
])
def test_attested_states_survive_without_repair_claims(state, reason, journal):
    source = _report(_batch([_row("placement", state, reason, path="/books/sample.epub")]))
    before = copy.deepcopy(source)
    diag = Diagnostics()
    diag.add_core_reconciliation_result(source)
    detail = diag.snapshot()[0]["detail"]
    batch = detail["placement_observations"]
    assert batch["observations"][0]["state"] == state
    assert batch["observations"][0]["reason"] == reason
    assert batch["observations"][0]["restoration"] == "not_evaluated"
    assert "deleted" not in json.dumps(detail)
    assert source == before


def test_partial_bounded_report_never_converts_unexamined_to_missing(journal):
    source = _report(_batch([
        _row("absent", path="/books/no.epub"),
        _row("not-examined", "unverified", "not_examined"),
    ], probe="partial", reasons=["stored_path_budget"]))
    source["stored_paths"] = {
        "status": "incomplete_scan", "reasons": ["stored_path_budget"],
        "counts": {"stored_path_missing": 1},
    }
    source["status"] = "incomplete_scan"
    source["reasons"] = ["stored_path_budget"]
    projection = public_fields(from_audit_result(source))
    assert projection["placement_observations"]["observations"][1]["state"] == "unverified"
    assert projection["stored_paths"]["counts"]["stored_path_missing"] == 1
    assert public_reconciliation_log_fields(public_reconciliation_log_message(projection)) == projection


def test_long_core_batch_is_explicitly_truncated_but_still_journaled(journal):
    rows = [
        _row(str(i), path="/books/" + "a" * 220 + str(i) + ".epub")
        for i in range(120)
    ]
    source = _report(_batch(rows))
    projected = public_fields(from_audit_result(source))
    assert projected["placement_observations"]["total_rows"] == 120
    actual = projected["placement_observations"]["observations"]
    assert 0 < len(actual) < 120
    assert projected["placement_observations"]["emitted_rows"] == len(actual)
    assert projected["placement_observations"]["truncated"] is True
    assert len(public_reconciliation_log_message(projected)) <= 16384
    diag = Diagnostics()
    diag.add_core_reconciliation_result(source)
    (event,) = query_journal(journal.path, search="library_reconciliation_audit")["events"]
    assert event["detail"]["placement_observations"]["emitted_rows"] == len(actual)


@pytest.mark.parametrize("tamper", [
    lambda b: b.update(scope_validated=False),
    lambda b: b.update(repair_authorized=True),
    lambda b: b.update(observation_ref="unsafe"),
    lambda b: b.update(emitted_rows=99),
    lambda b: b["observations"][0].update(state=["missing"]),
    lambda b: b["observations"][0].update(reason="restorable"),
    lambda b: b["observations"][0].update(placement_id="token=secret"),
])
def test_untrusted_or_contradictory_batch_is_not_promoted(tamper):
    source = _report(_batch([_row("known")]))
    tamper(source["placement_observations"])
    fields = from_audit_result(source)
    assert "placement_observations" not in fields
    assert fields["stored_paths"]["observation_source"] == "core_stored_paths"
    assert "placement_observations" not in public_fields(fields)


def test_invalidated_audit_never_includes_previous_scope_batch():
    source = _report(_batch([_row("alice-secret-id")]))
    source["status"] = "incomplete_scan"
    source["reasons"] = ["reconciliation_scope_changed"]
    result = from_audit_result(source)
    assert "placement_observations" not in result
