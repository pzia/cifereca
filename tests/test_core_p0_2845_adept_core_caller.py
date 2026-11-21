"""Closed attribution of synchronous Core ADEPT read boundaries (#2845)."""
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import pytest

from cifereca.core.diagnostic_adept import (
    adept_read_caller, from_runtime, public_fields, public_log_fields, log_message,
)
from cifereca.core.adept_inventory_reconciliation import reconcile_adept_acquisition
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from tests.test_core_p0_1143_opening_loans_redacted import _service
from tests.test_adept_shared_diagnostics import install_runtime, application_log


def read(operation="profile list", category="unattributed"):
    return {"operation": operation, "caller_category": category,
            "read_scope_ref": "rs_" + "ab" * 12, "duration_ms": 1,
            "returncode": 0}


def test_context_is_closed_operation_specific_and_restored():
    assert from_runtime(read(), "ok")["caller_category"] == "unattributed"
    forged = from_runtime(read(category="dashboard_opening"), "ok")
    assert "caller_category" not in forged
    with pytest.raises(ValueError):
        with adept_read_caller("PRIVATE"):
            pytest.fail("invalid context")
    with adept_read_caller("dashboard_opening"):
        fields = from_runtime(read(), "ok")
        assert fields["caller_category"] == "dashboard_opening"
        assert public_log_fields(log_message(fields)) == fields
        assert from_runtime(read(category="activation"), "ok")["caller_category"] == "activation"
        assert from_runtime(read("loans list"), "ok")["caller_category"] == "unattributed"
        assert "caller_category" not in from_runtime(read("download request"), "ok")
        assert public_fields({**fields, "operation": "loans list"}) == {}
        with ThreadPoolExecutor(max_workers=1) as worker:
            assert worker.submit(from_runtime, read(), "ok").result()["caller_category"] == "unattributed"
        with pytest.raises(RuntimeError):
            with adept_read_caller("reconciliation_inventory"):
                assert from_runtime(read("loans list"), "ok")["caller_category"] == "reconciliation_inventory"
                raise RuntimeError("synthetic")
        assert from_runtime(read(), "ok")["caller_category"] == "dashboard_opening"
    assert from_runtime(read(), "ok")["caller_category"] == "unattributed"


def test_actual_opening_dashboard_context_is_per_call(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        service.storage.adept_profile_binding_set("identity", "default", "PRIVATE_A")
        service.storage.adept_profile_binding_set("identity", "alex", "PRIVATE_B")
        events = []

        def scoped(identity_id, *, profile_name):
            events.append(from_runtime(read(), "ok"))
            return [{"profile_id": profile_name, "name": "fixture"}]

        monkeypatch.setattr(service, "adept_profiles_for_identity", scoped)
        service.opening_sync()
        assert [row["caller_category"] for row in events] == ["dashboard_opening"] * 2
        assert "PRIVATE" not in json.dumps(events)
        assert from_runtime(read(), "ok")["caller_category"] == "unattributed"
    finally:
        service.close()


def test_actual_reconciliation_read_boundaries_and_cache_unchanged(tmp_path):
    path = tmp_path / "synthetic.epub"
    path.write_bytes(b"fixture")
    events = []

    def candidates(*args, **kwargs):
        events.append(from_runtime(read(), "ok"))
        return [{"name": "PRIVATE_PROFILE"}]

    def lease(*args, **kwargs):
        events.append(from_runtime(read(), "ok"))
        def inventory(names):
            events.append(from_runtime(read(), "ok"))
            events.append(from_runtime(read("loans list"), "ok"))
            return {"profiles": [], "complete": True}
        return inventory

    service = SimpleNamespace(
        _adept_acquisition_path=lambda _: path,
        adept_inventory_candidates_for_acquisition=candidates,
        _adept_profile_name=lambda row: row["name"],
        adept_inventory_reader_for_acquisition=lease,
        storage=SimpleNamespace(acquisition_put=lambda _: None),
        diagnostics=SimpleNamespace(add=lambda *args, **kwargs: None),
    )
    row = {"acquisition_id": "PRIVATE_RECEIPT", "identity_id": "PRIVATE_A",
           "provider_id": "synthetic", "credential_id": "PRIVATE_C", "account_id": "PRIVATE_ACCOUNT"}
    cache = {}
    for _ in range(2):
        result = reconcile_adept_acquisition(
            service, row, cache=cache, match_local_book=lambda *_: [],
            AttentionRequired=RuntimeError, time=SimpleNamespace(time=lambda: 1),
        )
        assert result["status"] == "unresolved"
    assert [row["caller_category"] for row in events] == [
        "reconciliation_candidates", "reconciliation_lease",
        "reconciliation_inventory", "reconciliation_inventory",
        "reconciliation_candidates", "reconciliation_lease",
    ]
    assert "PRIVATE" not in json.dumps(events)
    assert from_runtime(read(), "ok")["caller_category"] == "unattributed"


def test_installed_runtime_callback_preserves_core_caller_on_both_channels(tmp_path, application_log):
    manager, context, diagnostics = install_runtime(tmp_path)
    try:
        with adept_read_caller("reconciliation_inventory"):
            context.report_diagnostic("PRIVATE", "ok", detail=read("loans list"), level="normal")
        rows = [row for row in query_diagnostic_events(
            diagnostics, log_path=application_log,
        )["events"] if row.get("detail", {}).get("operation_kind") == "kobo_adept"]
        assert {row["source"] for row in rows} == {"core_events", "app_log"}
        assert len(rows) == 2
        assert rows[0]["detail"] == rows[1]["detail"]
        assert rows[0]["detail"]["caller_category"] == "reconciliation_inventory"
        assert "PRIVATE" not in json.dumps(rows)
    finally:
        manager.close()


def test_service_read_consumers_are_distinct_without_policy_or_cache_changes():
    from cifereca.services import LibraryService
    from cifereca.core.adept_profiles import profile_name, profile_reference, profile_row

    events = []
    binding = ["PRIVATE_PROFILE_ID"]
    fixture = {"profile_id": "PRIVATE_PROFILE_ID", "name": "PRIVATE_PROFILE",
               "fetch_capable": True}

    def profiles():
        events.append(from_runtime(read(), "ok"))
        return [dict(fixture)]

    def inventory(names):
        assert names == ["PRIVATE_PROFILE"]
        events.append(from_runtime(read("loans list"), "ok"))
        return {"profiles": [], "complete": True}

    runtime = SimpleNamespace(loan_inventories=inventory)
    host = SimpleNamespace(
        kobo_profiles=profiles,
        storage=SimpleNamespace(
            adept_profile_binding_get=lambda *_: binding[0],
            adept_profile_binding_set=lambda *_: pytest.fail("unexpected binding mutation"),
            acquisition_get=lambda *_: {"status": "complete"},
        ),
        identity_by_id=lambda key: SimpleNamespace(identity_id=key),
        _adept_profile_row=lambda ref, rows: profile_row(ref, rows, list_profiles=profiles),
        _adept_profile_ref=profile_reference,
        _adept_profile_name=profile_name,
        _adept_inventory_candidates=lambda row, rows, refs: rows,
        _required_plugin_service=lambda _: runtime,
        _provider_loan_active_for_acquisition=lambda _: False,
    )
    saved = {"status": "ready", "identity_id": "PRIVATE_A",
             "adept_profile_id": "PRIVATE_PROFILE_ID"}
    assert LibraryService._inspect_source_adept_runtime(
        host, "PRIVATE_A", lambda: saved)["status"] == "observed"
    assert len(LibraryService.adept_profile_choices(host)) == 1
    assert LibraryService.resolve_adept_profile_for_identity(host, "PRIVATE_A") == (
        "PRIVATE_PROFILE", "identity")
    # Existing bound-origin fast path must remain a zero-read path.
    assert LibraryService._originating_adept_profile_id(
        host, "PRIVATE_A", "PRIVATE_PROFILE") == "PRIVATE_PROFILE_ID"
    assert len(events) == 3
    binding[0] = None
    assert LibraryService._originating_adept_profile_id(
        host, "PRIVATE_A", "PRIVATE_PROFILE") == "PRIVATE_PROFILE_ID"
    assert LibraryService.adept_loan_choices(host, "PRIVATE_ACQUISITION") == {
        "options": [], "complete": True}
    assert [event["caller_category"] for event in events] == [
        "source_runtime_inspection", "profile_choices",
        "execution_profile_resolution", "origin_profile_resolution",
        "loan_choices", "loan_choices",
    ]
    assert "PRIVATE" not in json.dumps(events)
    assert from_runtime(read(), "ok")["caller_category"] == "unattributed"


def test_service_inspection_failure_does_not_leak_read_context():
    from cifereca.services import LibraryService
    events = []
    def fail():
        event = read()
        event.update(error_code="kobo_adept_timeout", returncode=1, retryable=True)
        events.append(from_runtime(event, "error"))
        raise RuntimeError("PRIVATE")
    host = SimpleNamespace(kobo_profiles=fail)
    saved = {"status": "ready", "identity_id": "PRIVATE_A",
             "adept_profile_id": "PRIVATE_PROFILE_ID"}
    result = LibraryService._inspect_source_adept_runtime(host, "PRIVATE_A", lambda: saved)
    assert result["reason"] == "adept_runtime_unavailable"
    assert events[0]["caller_category"] == "source_runtime_inspection"
    assert events[0]["code"] == "kobo_adept_timeout"
    assert events[0]["retryable"] is True
    assert "PRIVATE" not in json.dumps(events)
    assert from_runtime(read(), "ok")["caller_category"] == "unattributed"
