"""P1 #4462: a completed scheduled Core sync is not a cancelled GTK task.

All providers and identities are synthetic. Exercise both scheduled worker
callbacks against the production TaskRunner classifier; no network or files.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import numilog_materialization_startup as backup
from cifereca.gui import source_inventory_startup as startup
from cifereca.plugin_api import SyncResult
from tests.test_core_p0_1346_source_inventory_timer import rig, SOURCE_ID
from tests.test_core_p0_981_redacted_task_traces import _runner


def _scheduled_window(provider: str, trigger: str):
    source = NS(
        identity_id="alice", source_id=SOURCE_ID, provider_id=provider,
        enabled=True, connected=True, sync_capable=True,
        status="idle", last_attempt="",
        sync_capabilities=("scheduled_refresh",),
        phase_states=(NS(
            phase="discover_rights", status="idle", last_attempt="",
        ),),
    )
    return rig(
        accounts=[source],
        modes={SOURCE_ID: {"mode": "on_start" if trigger == "startup"
                           else "periodic", "interval_minutes": 15}},
    )


def _dispatch(r, trigger: str, monkeypatch):
    if trigger == "startup":
        # Materialization is an independent optional worker. This test never
        # activates downloads; inspect only the inventory's task receipt.
        monkeypatch.setattr(backup, "dispatch_after_opening", lambda _w: True)
        assert startup.dispatch_after_opening(r.window) is True
    else:
        assert r.configure() is False
        tick = next(iter(r.records["timers"].values()))
        assert tick() is True


@pytest.mark.parametrize("provider", ["kobo", "numilog"])
@pytest.mark.parametrize("trigger", ["startup", "periodic"])
@pytest.mark.parametrize("invalidate_after_core", [False, True])
def test_attested_scheduled_core_success_survives_worker_and_stale_gui_scope(
    monkeypatch, caplog, provider, trigger, invalidate_after_core,
):
    r = _scheduled_window(provider, trigger)
    runner, displayed, flush, logger = _runner()
    r.window.runner = runner
    executed = []

    def completed(identity, schedule):
        executed.append((identity, schedule))
        # Core already completed. A GTK owner change after this point must
        # not convert its completion to business_status=cancelled.
        if invalidate_after_core:
            r.window.plugin_manager.broker = lambda _name: None
        return SyncResult(ok=True, facts={
            "phase": "discover_rights", "attempted": 1, "failed": 0,
        })

    r.broker.synchronize_scheduled_inventory = completed
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            _dispatch(r, trigger, monkeypatch)
            flush()
        assert executed == [("alice", trigger)]
        finished = [args for event, args in displayed if event == "finished"]
        assert len(finished) == 1
        assert finished[0][3] is True
        assert len(finished[0]) == 5  # successful GTK task has no extra error code
        assert "kind=source-sync" in caplog.text
        assert "status=success business_status=success" in caplog.text
        assert "business_status=cancelled" not in caplog.text
    finally:
        runner.close()


@pytest.mark.parametrize("trigger", ["startup", "periodic"])
def test_broker_skips_every_scheduled_attempt_without_invented_success(
    monkeypatch, caplog, trigger,
):
    r = _scheduled_window("numilog", trigger)
    runner, displayed, flush, logger = _runner()
    r.window.runner = runner
    r.broker.synchronize_scheduled_inventory = lambda *_args: SyncResult(
        ok=True, facts={"attempted": 0, "failed": 0},
    )
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            _dispatch(r, trigger, monkeypatch)
            flush()
        finished = [args for event, args in displayed if event == "finished"]
        assert len(finished) == 1
        assert finished[0][3] is False
        assert "status=success business_status=success" not in caplog.text
    finally:
        runner.close()


@pytest.mark.parametrize(("receipt", "expected"), [
    (SyncResult(ok=True), "success"),
    (SyncResult(ok=False, facts={"reason": "operation_busy"}), "busy"),
    (SyncResult(ok=False, facts={"status": "cancelled"}), "cancelled"),
    (SyncResult(ok=True, facts={
        "status": "partial",
        "canonical_execution": {"pending": 7, "blocked": 0, "failed": 3},
    }), "partial"),
])
def test_source_sync_core_receipt_is_authoritative_for_gui_activity(
    caplog, receipt, expected,
):
    runner, displayed, flush, logger = _runner()
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Source", lambda: receipt, kind="source-sync",
                origin="automatic",
            )
            flush()
        finished = [args for event, args in displayed if event == "finished"]
        assert len(finished) == 1
        assert finished[0][3] is (expected == "success")
        if expected == "success":
            assert len(finished[0]) == 5
        else:
            assert finished[0][-1] == expected
        assert f"business_status={expected}" in caplog.text
        assert ("status=success" in caplog.text) is (expected == "success")
    finally:
        runner.close()
