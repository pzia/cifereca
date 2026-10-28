"""#3638: project existing read-only Core observations, no second scanner."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import threading
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_journal import query_journal, journal_text_export
from cifereca.core.diagnostic_reconciliation import (
    from_audit_result, public_fields, public_reconciliation_log_message,
    public_reconciliation_log_fields,
)
from cifereca.core.library_reconciliation import EvidenceSnapshot
from cifereca.core.library_stored_paths import inspect_stored_paths
from cifereca.diagnostic_task_context import diagnostic_task_scope
from cifereca.diagnostics import Diagnostics
from cifereca.gui.diagnostics_pseudonyms import DiagnosticsPseudonyms
from tests.test_diagnostics_acquisition_fidelity import journal
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, new_source, register, target, publish,
)


def report(stored, *, reasons=(), inventory_status="complete"):
    all_reasons = sorted(set(reasons) | set(stored.get("reasons", [])))
    return {"status": "incomplete_scan" if all_reasons else "complete",
            "reasons": all_reasons,
            "inventory": {"status": inventory_status,
                          "reasons": ["file_budget"] if inventory_status == "incomplete_scan" else []},
            "stored_paths": stored}


def placement(path, profile="A", name="p-A", state="present"):
    return {"placement_id": name, "artifact_id": "sha256:observed-artifact",
            "identity_id": profile, "path": str(path), "state": state}


@pytest.mark.parametrize("scenario,expected_state,coverage", [
    ("missing", "stored_path_missing", "complete"),
    ("mount_unavailable", "stored_path_unavailable", "partial"),
    ("permission_denied", "stored_path_unavailable", "partial"),
    ("foreign_profile", "stored_path_out_of_scope", "complete"),
    ("cancelled", None, "partial"),
])
def test_existing_core_probe_projection_never_relabels_unknown_as_missing(
    tmp_path, monkeypatch, scenario, expected_state, coverage,
):
    root = tmp_path / "A"
    if scenario != "mount_unavailable":
        root.mkdir()
    rows = [placement(root / "never-created.epub", profile="B" if scenario == "foreign_profile" else "A")]
    snapshot = EvidenceSnapshot({"placements": rows})
    original = copy.deepcopy(snapshot)
    if scenario == "permission_denied":
        def denied(_root):
            raise PermissionError("synthetic denied")
        monkeypatch.setattr("cifereca.core.library_stored_paths._open_root", denied)
    observed = inspect_stored_paths(snapshot, {"A": (root,)}, cancelled=lambda: scenario == "cancelled")
    assert observed.states == ({0: expected_state} if expected_state else {})
    source = report(observed.redacted())
    fields = public_fields(from_audit_result(source))
    details = fields["stored_paths"]
    assert details["coverage"] == coverage
    assert details["counts"] == observed.redacted()["counts"]
    assert (details["counts"].get("stored_path_missing", 0) > 0) == (scenario == "missing")
    assert details["scope"] == "not_supplied"  # No profile ID supplied to Diagnostics.
    assert snapshot == original
    assert not (root / "never-created.epub").exists()
    assert "restorable" not in json.dumps(fields) and "deleted" not in json.dumps(fields)


def test_partial_inventory_and_bounded_probe_keep_distinct_coverages(tmp_path):
    root = tmp_path / "A"
    root.mkdir()
    snapshot = EvidenceSnapshot({"placements": [placement(root / "absent.epub"), placement(root / "not-probed.epub", name="p-2")]})
    observed = inspect_stored_paths(snapshot, {"A": (root,)}, max_paths=1)
    assert observed.states == {0: "stored_path_missing"}
    fields = from_audit_result(report(observed.redacted(), reasons=["file_budget"], inventory_status="incomplete_scan"))
    assert fields["status"] == "partial"
    assert fields["stored_paths"]["coverage"] == "partial"
    assert fields["stored_paths"]["inventory_coverage"] == "partial"
    assert fields["stored_paths"]["counts"] == {"stored_path_missing": 1}
    assert fields["stored_paths"]["missing_path_context"]["distinct_placements_with_id"] == 1
    # The second unexamined row is not counted missing or absent-by-default.
    assert "unverified" not in fields["stored_paths"]["counts"]


def test_direct_absence_observation_does_not_require_exhaustive_inventory(tmp_path):
    root = tmp_path / "A"
    root.mkdir()
    observed = inspect_stored_paths(EvidenceSnapshot({"placements": [placement(root / "missing.epub")]}), {"A": (root,)})
    fields = from_audit_result(report(observed.redacted(), reasons=["file_budget"], inventory_status="incomplete_scan"))
    assert fields["stored_paths"]["coverage"] == "complete"
    assert fields["stored_paths"]["inventory_coverage"] == "partial"
    assert fields["stored_paths"]["counts"] == {"stored_path_missing": 1}


@pytest.mark.parametrize("reason", ["reconciliation_scope_changed", "reconciliation_identity_not_active", "unknown_reconciliation_identity"])
def test_profile_switch_does_not_publish_successful_scope(reason):
    fields = from_audit_result(report({"status": "complete", "reasons": [], "counts": {}}, reasons=[reason]))
    assert fields["status"] == "failed"
    assert fields["stored_paths"]["scope"] == "invalidated"
    assert fields["stored_paths"]["counts"] == {}
    assert "identity_id" not in fields


def test_missing_historical_and_active_context_survives_all_diagnostic_surfaces(journal, tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    rows = [placement(root / "active.epub"), placement(root / "old.epub", name="p-old", state="missing")]
    observed = inspect_stored_paths(EvidenceSnapshot({"placements": rows}), {"A": (root,)})
    source = report(observed.redacted())
    original = copy.deepcopy(source)
    diag = Diagnostics()
    with diagnostic_task_scope():
        diag.add_core_reconciliation_result(source, duration_ms=123)
    (event,) = query_journal(journal.path, search="library_reconciliation_audit")["events"]
    detail = event["detail"]
    assert detail["stored_paths"]["missing_path_context"] == {
        "sqlite_present": 1, "sqlite_missing": 1, "sqlite_other": 0,
        "distinct_paths": 2, "distinct_artifacts_with_id": 1, "distinct_placements_with_id": 2,
    }
    assert event["task_ref"] == detail["task_ref"] == diag.snapshot()[0]["task_ref"]
    assert event["severity"] == "info"  # Completed audit does not mean every file is present.
    assert diag.snapshot()[0]["detail"] == detail
    assert query_diagnostic_events(diag, source="core_events", log_path=tmp_path / "none")["events"][0]["detail"] == detail
    body = public_reconciliation_log_message(detail)
    assert public_reconciliation_log_fields(body) == detail
    legacy = tmp_path / "legacy.log"
    legacy.write_text("2026-10-28 20:00:00,000 INFO cifereca [MainThread] " + body + "\n")
    (old,) = query_diagnostic_events(Diagnostics(), source="app_log", log_path=legacy)["events"]
    assert old["detail"] == detail and old["task_ref"] == event["task_ref"]
    assert journal.path.read_text() == journal.console.getvalue()
    assert journal.path.stat().st_mode & 0o777 == 0o600
    broker = DiagnosticsBrokerImpl(NS(diagnostics=diag))
    for alias in ("all", "app_log", "core_events"):
        assert broker.events(source=alias, search="stored_path_missing")["events"] == [event]
    exported = json.loads(broker.export_raw_journal())
    assert event in exported["events"]
    ndjson = [json.loads(line) for line in journal_text_export(journal.path, version="test").splitlines() if line.startswith("{")]
    assert event in ndjson
    assert DiagnosticsPseudonyms().text(json.dumps(detail)) == json.dumps(detail)
    assert source == original
    assert "p-A" not in journal.path.read_text()  # Not supplied by Core's summary.


def test_worker_references_do_not_correlate_distinct_audits(journal):
    diag = Diagnostics()
    source = report({"status": "complete", "reasons": [], "counts": {}})
    barrier = threading.Barrier(2)
    def emit(_):
        with diagnostic_task_scope():
            barrier.wait(timeout=5)
            diag.add_core_reconciliation_result(source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(emit, range(2)))
    events = query_journal(journal.path, search="library_reconciliation_audit")["events"]
    assert len(events) == 2 and len({event["task_ref"] for event in events}) == 2
    diag.add_core_reconciliation_result(source)
    assert diag.snapshot()[-1].get("task_ref") is None


@pytest.mark.parametrize("stored", [None, {}, {"status": []}, {"status": "unknown", "counts": {"stored_path_missing": True}}])
def test_unknown_measurement_is_not_silently_zero_or_complete(stored):
    fields = from_audit_result({"status": "incomplete_scan", "reasons": ["unclassified_limit"], "stored_paths": stored})
    assert fields["stored_paths"]["coverage"] == "unverified"
    assert fields["stored_paths"]["counts"] == {}
    assert fields["stored_paths"]["missing_path_context"] == {}
    assert public_fields(fields) == fields


def test_only_measurements_enter_summary_not_credentials_or_invented_ids():
    source = report({"status": "complete", "reasons": [], "counts": {"stored_path_missing": 3, "token": "test-secret"},
                     "missing_path_context": {"sqlite_present": 3, "sqlite_missing": False},
                     "path": "https://example.invalid/book?sig=test-secret", "password": "test-secret",
                     "placement_id": "not-attested", "artifact_id": "not-attested"})
    fields = from_audit_result(source)
    encoded = json.dumps(fields)
    assert "test-secret" not in encoded and "not-attested" not in encoded
    assert fields["stored_paths"]["counts"] == {"stored_path_missing": 3}
    assert fields["stored_paths"]["missing_path_context"] == {"sqlite_present": 3}
    assert public_reconciliation_log_fields(public_reconciliation_log_message(fields)) == fields


@pytest.mark.parametrize("task_ref", [None, True, "not-a-worker", "A" * 12])
def test_unattested_task_reference_rejected(task_ref):
    fields = from_audit_result({"status": "complete", "reasons": []})
    fields["task_ref"] = task_ref
    assert public_fields(fields) == {}


def test_sqlite_profiles_artifact_and_provider_transfer_history_remain_intact(store, tmp_path):
    """Real v14 fixture; only setup writes. No file is removed or restored."""
    from cifereca.core.library_audit import audit_library
    from tests.test_library_reconciliation import filesystem

    root = tmp_path / "EBooks"
    for profile in ("A", "B"):
        register(store, profile, "seller", "card-" + profile, account="buyer-" + profile)
        source, snap = new_source(profile, credential="card-" + profile, account="buyer-" + profile)
        result = publish(store, root, target(tmp_path, profile), source, snap,
                         adept_receipt_ref="historical-adept-" + profile)
    artifact = result.artifact_id
    (root / "C").mkdir()
    store.identity_put({"identity_id": "C", "directory_name": "C", "label": "C"})
    # Model SQLite pointing at never-created paths, without deleting a file.
    store.placement_put({"placement_id": "transfer-C", "identity_id": "C",
                         "artifact_id": artifact, "path": str(root / "C" / "missing.pdf"), "state": "missing"})
    with store._lock, store._connect() as db:
        db.execute("UPDATE placements SET path=? WHERE identity_id='A'", (str(root / "A" / "missing.pdf"),))
        db.execute("INSERT INTO origin_links(origin_link_id,origin_kind,identity_id,artifact_id,from_identity_id,evidence_ref,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                   ("B-to-C", "transfer", "C", artifact, "B", "one-time-copy", 1, 1))
    def saved_rows():
        with store._lock, store._connect() as db:
            return {table: sorted(db.execute("SELECT * FROM " + table).fetchall())
                    for table in ("artifacts", "placements", "source_references", "origin_links", "entitlements")}
    before = saved_rows()
    store.close()  # End fixture setup; audit a quiescent DB, no WAL dependency.
    database_bytes = store.path.read_bytes()
    files = filesystem(root)
    diag = Diagnostics()
    for profile, expected in (("A", "stored_path_missing"), ("B", "stored_path_present"), ("C", "stored_path_missing")):
        audit = audit_library(store.path, {profile: (root / profile,)})
        assert "placements" in audit.snapshot.tables, audit.snapshot.reasons
        selected = audit.snapshot.tables["placements"]
        assert all(row["identity_id"] == profile for row in selected)
        assert len(selected) == 1
        assert list(audit.stored_paths.states.values()) == [expected]
        diag.add_core_reconciliation_result(audit.redacted())
        assert diag.snapshot()[-1]["detail"]["stored_paths"]["counts"] == {expected: 1}
        assert "restorable" not in json.dumps(diag.snapshot()[-1])
    assert store.path.read_bytes() == database_bytes
    assert saved_rows() == before
    assert filesystem(root) == files
    assert len(before["artifacts"]) == 1 and len(before["placements"]) == 3
    assert len(before["source_references"]) == 2  # C transfer is not a provider right.
    assert len(before["origin_links"]) == 5  # Two purchases + two ADEPT + transfer.


def test_diagnostics_cannot_promote_transfer_to_provider_restoration(journal):
    from cifereca.core.source_convergence import OriginEvidence
    from tests.test_core_p0_3171_source_convergence_plan import (
        source, origin, scan, plan, SHA_A,
    )
    a, ak = source()
    b, bk = source("provider-beta", "B", "cred-B", "account-B", right="purchase-B")
    origins = (origin(ak), origin(bk),
               OriginEvidence("transfer", SHA_A, identity_id="C", origin_ref="B-to-C"))
    before = plan(a, b, origins=origins, scans=(scan("A"), scan("B")), consent=(ak, bk))
    assert [decision.state for decision in before.decisions] == ["missing_to_download", "missing_to_download"]
    diag = Diagnostics()
    diag.add_core_reconciliation_result(report({"status": "complete", "reasons": [], "counts": {"stored_path_missing": 3}}))
    after = plan(a, b, origins=origins, scans=(scan("A"), scan("B")), consent=(ak, bk))
    assert after == before and all(row.source.scope.identity_id != "C" for row in after.decisions)
    assert not after.acquisition_authorized


def test_full_bounded_path_summary_roundtrips_without_losing_large_reason_set():
    from cifereca.core.diagnostic_reconciliation import _REASONS
    reasons = sorted(_REASONS)
    source = report({
        "status": "incomplete_scan", "reasons": reasons,
        "counts": {"stored_path_missing": 10**12},
        "missing_path_context": {"distinct_paths": 10**12},
    }, inventory_status="incomplete_scan")
    fields = from_audit_result(source, 600000)
    fields["task_ref"] = "a" * 12
    assert public_fields(fields) == fields
    assert public_reconciliation_log_fields(public_reconciliation_log_message(fields)) == fields
