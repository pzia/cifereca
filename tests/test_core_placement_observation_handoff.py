"""#2847/#3638: Core-only facts from the existing read-only audit."""
from __future__ import annotations

import json
import errno
import os
import sqlite3

import pytest

from cifereca.core import library_audit_broker as broker
from cifereca.core.library_audit import audit_library
from cifereca.core.library_placement_observations import placement_observations, MAX_OBSERVATIONS
from cifereca.diagnostics import Diagnostics
from tests.test_library_audit_broker import scoped
from tests.test_library_reconciliation import filesystem


def add_rows(database, rows):
    with sqlite3.connect(database) as db:
        db.executemany(
            "INSERT INTO placements(placement_id,artifact_id,path,identity_id,provider_id,source_item_id,state) VALUES(?,?,?,?,?,?,?)",
            [(pid, artifact, str(path), identity, "synthetic-provider", "edition", state)
             for pid, artifact, path, identity, state in rows],
        )


def capture(service, monkeypatch, *, forward=True):
    diag = service.diagnostics = Diagnostics()
    received = []
    original = diag.add_core_reconciliation_result
    def receive(result, **kwargs):
        received.append(result)
        if forward:
            original(result, **kwargs)
    monkeypatch.setattr(diag, "add_core_reconciliation_result", receive)
    return received


def test_attested_ids_and_states_reach_private_diagnostics_argument_not_plugin_result(scoped, monkeypatch, tmp_path):
    service, database, root = scoped
    add_rows(database, [
        ("placement-alice", "sha256:artifact-A", root / "a" / "missing.epub", "alice", "missing"),
        ("placement-present", "sha256:artifact-A", root / "a" / "PRIVATE.epub", "alice", "present"),
        ("placement-bob", "sha256:artifact-A", root / "b" / "missing.epub", "bob", "missing"),
    ])
    received = capture(service, monkeypatch)
    before = filesystem(tmp_path)
    result = broker.LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    assert filesystem(tmp_path) == before
    private, = received
    batch = private["placement_observations"]
    assert batch["identity_id"] == "alice" and batch["scope_validated"] is True
    assert batch["schema"] == "cifereca.placement.observation/1"
    assert batch["read_only"] and not batch["repair_authorized"] and not batch["acquisition_authorized"]
    assert batch["total_rows"] == batch["emitted_rows"] == 2 and not batch["truncated"]
    facts = {row["placement_id"]: row for row in batch["observations"]}
    assert facts["placement-alice"]["artifact_id"] == "sha256:artifact-A"
    assert facts["placement-alice"]["path"] == str(root / "a" / "missing.epub")
    assert facts["placement-alice"]["state"] == "missing"
    assert facts["placement-alice"]["sqlite_state"] == "missing"
    assert facts["placement-alice"]["reason"] == "enoent_observed"
    assert facts["placement-present"]["state"] == "present"
    assert all(row["restoration"] == "not_evaluated" for row in facts.values())
    assert "placement-bob" not in json.dumps(batch)
    assert "placement_observations" not in result and "PRIVATE" not in json.dumps(result)
    # C's current projection remains unchanged until C integrates this handoff.
    assert "placement_observations" not in json.dumps(service.diagnostics.snapshot())


@pytest.mark.parametrize("reason", ["unmounted", "permission_denied"])
def test_unavailable_root_is_not_reported_missing(scoped, monkeypatch, reason):
    service, database, root = scoped
    if reason == "unmounted":
        root = root.parent / "never-mounted"
        service.config.data["library"]["directory"] = str(root)
    else:
        def denied(_):
            raise PermissionError("synthetic inaccessible root")
        monkeypatch.setattr("cifereca.core.library_stored_paths._open_root", denied)
    add_rows(database, [("p", "a", root / "a" / "missing.epub", "alice", "present")])
    received = capture(service, monkeypatch)
    broker.LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    batch = received[0]["placement_observations"]
    fact, = batch["observations"]
    assert fact["state"] == "unavailable"
    assert fact["reason"] == ("root_missing" if reason == "unmounted" else "root_permission_denied")
    # No mount-table read was performed, so do not claim the cause of ENOENT.
    assert "errno" not in fact and batch["probe_coverage"] == "partial"


@pytest.mark.parametrize("scenario,state,reason", [
    ("leaf_symlink", "unavailable", "not_regular_file"),
    ("parent_symlink", "unavailable", "symlink_or_not_directory"),
    ("leaf_permission", "unavailable", "permission_denied"),
    ("root_changed", "unavailable", "root_changed"),
    ("outside_root", "unverified", "out_of_scope"),
])
def test_probe_reasons_are_observed_without_new_access_or_absence_inference(scoped, monkeypatch, scenario, state, reason):
    service, database, root = scoped
    path = root / "a" / "blocked.epub"
    if scenario == "leaf_symlink":
        path.symlink_to(root / "b" / "PRIVATE.epub")
    elif scenario == "parent_symlink":
        (root / "a" / "link").symlink_to(root / "b", target_is_directory=True)
        path = root / "a" / "link" / "PRIVATE.epub"
    elif scenario == "leaf_permission":
        original = os.stat
        def denied(name, *args, **kwargs):
            if name == "blocked.epub" and kwargs.get("dir_fd") is not None:
                raise PermissionError(errno.EACCES, "synthetic leaf permission")
            return original(name, *args, **kwargs)
        monkeypatch.setattr(os, "stat", denied)
    elif scenario == "root_changed":
        import cifereca.core.library_stored_paths as stored
        original = stored._open_root
        calls = []
        def swapped(name):
            calls.append(name)
            return original(name if len(calls) == 1 else root / "b")
        monkeypatch.setattr(stored, "_open_root", swapped)
    elif scenario == "outside_root":
        path = root / "b" / "PRIVATE.epub"
    add_rows(database, [("p", "a", path, "alice", "present")])
    received = capture(service, monkeypatch)
    broker.LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    fact, = received[0]["placement_observations"]["observations"]
    assert (fact["state"], fact["reason"]) == (state, reason)
    assert fact["restoration"] == "not_evaluated"


def test_partial_probe_preserves_unexamined_row_as_unverified(scoped, monkeypatch):
    service, database, root = scoped
    add_rows(database, [(str(i), "a", root / "a" / f"missing-{i}.epub", "alice", "present") for i in range(2)])
    import cifereca.core.library_audit as audit_module
    original = audit_module.inspect_stored_paths
    monkeypatch.setattr(audit_module, "inspect_stored_paths", lambda *args, **kwargs: original(*args, **kwargs, max_paths=1))
    received = capture(service, monkeypatch)
    broker.LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    batch = received[0]["placement_observations"]
    assert [row["state"] for row in batch["observations"]] == ["missing", "unverified"]
    assert batch["observations"][1]["reason"] == "not_examined"
    assert batch["probe_coverage"] == "partial" and batch["inventory_coverage"] == "complete"


@pytest.mark.parametrize("change", ["profile", "source"])
def test_post_finish_scope_or_snapshot_change_withholds_all_private_facts(scoped, monkeypatch, change):
    service, database, root = scoped
    add_rows(database, [("p", "a", root / "a" / "missing.epub", "alice", "present")])
    received = capture(service, monkeypatch)
    execute = broker._execute_scoped_audit
    def mutate(*args):
        result = execute(*args)
        if change == "profile":
            service._account_context_epoch += 1
        else:
            stamp = database.stat()
            os.utime(database, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
        return result
    monkeypatch.setattr(broker, "_execute_scoped_audit", mutate)
    result = broker.LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    assert "placement_observations" not in received[0]
    assert "placement_observations" not in result


@pytest.mark.parametrize("change", ["profile", "source"])
def test_change_during_fact_projection_withholds_envelope(scoped, monkeypatch, change):
    service, database, root = scoped
    add_rows(database, [("p", "a", root / "a" / "missing.epub", "alice", "present")])
    received = capture(service, monkeypatch)
    import cifereca.core.library_placement_observations as facts_module
    original = facts_module.placement_observations
    def mutate(*args):
        result = original(*args)
        if change == "profile":
            service._account_context_epoch += 1
        else:
            stamp = database.stat()
            os.utime(database, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
        return result
    monkeypatch.setattr(facts_module, "placement_observations", mutate)
    result = broker.LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    assert "placement_observations" not in received[0]
    assert "placement_observations" not in result


def test_bound_is_explicit_and_projection_performs_no_new_io(scoped, monkeypatch):
    service, database, root = scoped
    count = MAX_OBSERVATIONS + 2
    add_rows(database, [(str(i), "same-artifact", root / "a" / f"missing-{i}.epub", "alice", "missing") for i in range(count)])
    audit = audit_library(database, {"alice": (root / "a",)})
    monkeypatch.setattr(os, "stat", lambda *_a, **_kw: pytest.fail("projection must not stat"))
    monkeypatch.setattr(os, "open", lambda *_a, **_kw: pytest.fail("projection must not open"))
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_kw: pytest.fail("projection must not query SQLite"))
    batch = placement_observations(audit, "alice")
    assert batch["total_rows"] == count and batch["emitted_rows"] == MAX_OBSERVATIONS
    assert batch["truncated"] is True
    assert len(batch["observation_ref"]) == 32
    assert all(row["state"] == "missing" for row in batch["observations"])


def test_diagnostic_copy_isolated_and_missing_ids_not_invented(scoped, monkeypatch):
    service, database, root = scoped
    add_rows(database, [(None, None, root / "a" / "missing.epub", "alice", "present")])
    received = capture(service, monkeypatch, forward=False)
    original = service.diagnostics.add_core_reconciliation_result
    def alter(result, **kwargs):
        original(result, **kwargs)
        result["stored_paths"]["counts"]["forged"] = 7
    monkeypatch.setattr(service.diagnostics, "add_core_reconciliation_result", alter)
    result = broker.LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    assert "forged" not in result["stored_paths"]["counts"]
    row, = received[0]["placement_observations"]["observations"]
    assert "placement_id" not in row and "artifact_id" not in row


def test_credential_values_in_path_are_protected_without_hiding_ordinary_ids(scoped, monkeypatch):
    service, database, root = scoped
    add_rows(database, [("ordinary-placement", "ordinary-artifact", root / "a" / "password=fixture-secret", "alice", "missing")])
    received = capture(service, monkeypatch)
    broker.LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    row, = received[0]["placement_observations"]["observations"]
    assert "fixture-secret" not in row["path"] and row["path_credentials_removed"] is True
    assert row["placement_id"] == "ordinary-placement" and row["artifact_id"] == "ordinary-artifact"
