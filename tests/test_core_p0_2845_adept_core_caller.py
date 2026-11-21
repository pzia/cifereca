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
