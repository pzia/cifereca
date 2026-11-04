"""#4462: automatic source sync must retain the attested Core outcome.

Synthetic scheduled broker and headless production TaskRunner; no HTTP,
provider account, file acquisition or live GTK session.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS

import pytest

from cifereca.diagnostic_task_context import (
    diagnostic_task_reference, diagnostic_task_scope,
)
from cifereca.gui import source_inventory_startup as startup
from cifereca.gui.activity_feedback import ActivityFeedback
from cifereca.plugin_api import Cancelled, SyncResult
from tests.test_core_p0_1346_kobo_on_start_inventory_gui import _phase, _rig
from tests.test_core_p0_1346_source_inventory_timer import (
    SOURCE_ID, rig,
)
from tests.test_core_p0_981_redacted_task_traces import _runner


def _scheduled(trigger: str, *, provider: str = "kobo", accounts=None):
    if trigger == "startup":
        r = _rig(accounts=accounts)
        if accounts is None:
            r.broker.sources("alice")[0].provider_id = provider
        assert startup.dispatch_after_opening(r.window)
    else:
        if accounts is None:
            r = rig(modes={
                SOURCE_ID: {"mode": "periodic", "interval_minutes": 15},
            })
            r.broker.sources("alice")[0].provider_id = provider
        else:
            r = rig(accounts=accounts, modes={
                row.source_id: {
                    "mode": "periodic", "interval_minutes": 15,
                } for row in accounts
            })
        r.configure()
        assert next(iter(r.records["timers"].values()))() is True
    assert len(r.jobs) == 1
    assert r.jobs[0].options["kind"] == "source-sync"
    return r, r.jobs[0]


def _activity_from_finish(args):
    feedback = ActivityFeedback()
    status = args[5] if len(args) > 5 else None
    return feedback.finished(
        args[0], args[2], "source-sync",
        success=args[3], error=args[4] or "",
        business_status=status,
    )


@pytest.mark.parametrize("trigger", ("startup", "periodic"))
@pytest.mark.parametrize("provider", ("kobo", "numilog"))
def test_attested_automatic_core_success_survives_worker_and_activity(
    caplog, trigger, provider,
):
    r, job = _scheduled(trigger, provider=provider)
    receipt = SyncResult(
        ok=True, changed=2,
        facts={"phase": "discover_rights", "attempted": 1, "failed": 0},
    )
    calls = []

    def dispatch(identity, observed_trigger):
        calls.append((identity, observed_trigger))
        # Reproduce the correlated Core terminal evidence without network.
        with diagnostic_task_scope():
            reference = diagnostic_task_reference()
            logging.getLogger("cifereca").info(
                "source_sync_complete status=success provider_id=%s task_ref=%s",
                provider, reference,
            )
        return receipt

    r.broker.synchronize_scheduled_inventory = dispatch
    runner, displayed, flush, logger = _runner()
    delivered = []
    try:
        with caplog.at_level(logging.INFO):
            runner.submit(
                job.label, job.work, ok=delivered.append,
                kind="source-sync", origin="automatic",
            )
            flush()
        assert calls == [("alice", trigger)]
        assert delivered == [receipt]
        finishes = [args for event, args in displayed if event == "finished"]
        assert len(finishes) == 1
        assert finishes[0][3:5] == (True, None)
        activity = _activity_from_finish(finishes[0])
        assert activity.state == "success"
        assert activity.display_text.startswith("Terminé")
        assert "source_sync_complete status=success" in caplog.text
        assert "TRACE task correlation task_ref=" in caplog.text
        assert "TRACE task outcome id=1 kind=source-sync " in caplog.text
        assert "status=success business_status=success" in caplog.text
        assert "business_status=cancelled" not in caplog.text
    finally:
        runner.close()


@pytest.mark.parametrize("trigger", ("startup", "periodic"))
def test_scope_expiring_after_first_core_success_does_not_erase_it(trigger):
    accounts = [
        NS(identity_id=identity, source_id="sync:" + letter * 24,
           provider_id="kobo", enabled=True, connected=True,
           sync_capable=True, status="idle", last_attempt="",
           phase_states=(_phase(),))
        for identity, letter in (("alice", "a"), ("bob", "b"))
    ]
    r, job = _scheduled(trigger, accounts=accounts)
    receipt = SyncResult(ok=True, changed=1, facts={"attempted": 1})
    called = []

    def dispatch(identity, _trigger):
        called.append(identity)
        r.window._closed = True  # The next identity loses GTK authority.
        return receipt

    r.broker.synchronize_scheduled_inventory = dispatch
    assert job.work() is receipt
    assert called == ["alice"]


@pytest.mark.parametrize("trigger", ("startup", "periodic"))
def test_no_core_invocation_does_not_claim_success(trigger):
    r, job = _scheduled(trigger)
    called = []
    r.broker.synchronize_scheduled_inventory = lambda *_args: called.append(1)
    r.window._closed = True
    assert job.work() is None
    assert called == []


@pytest.mark.parametrize(("receipt", "state", "message"), [
    (SyncResult(ok=False, facts={"reason": "operation_busy"}),
     "busy", "Synchronisation déjà en cours"),
    (SyncResult(ok=False, facts={"status": "cancelled"}),
     "cancelled", "Synchronisation annulée"),
    (SyncResult(ok=False, facts={
        "status": "partial",
        "canonical_execution": {"pending": 7, "blocked": 0, "failed": 3},
    }), "partial", "Synchronisation partielle"),
])
def test_real_business_refusal_remains_typed_in_task_and_activity(
    caplog, receipt, state, message,
):
    runner, displayed, flush, logger = _runner()
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Inventaire Kobo", lambda: receipt,
                kind="source-sync", origin="automatic",
            )
            flush()
        finish = [args for event, args in displayed if event == "finished"]
        assert len(finish) == 1
        assert finish[0][3:5] == (False, message)
        assert _activity_from_finish(finish[0]).state == state
        assert f"status=failed business_status={state}" in caplog.text
    finally:
        runner.close()


def test_unexecuted_none_and_explicit_cancelled_are_not_fake_successes(caplog):
    runner, displayed, flush, logger = _runner()
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Scope révoqué", lambda: None,
                kind="source-sync", origin="automatic",
            )
            flush()
        first = [args for event, args in displayed if event == "finished"][-1]
        assert first[3] is False
        assert _activity_from_finish(first).state == "cancelled"
        assert "status=failed business_status=cancelled" in caplog.text

        def cancelled():
            raise Cancelled("user request")

        displayed.clear()
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Annulation demandée", cancelled,
                kind="source-sync", origin="automatic",
            )
            flush()
        second = [args for event, args in displayed if event == "finished"][-1]
        assert second[3] is False
        assert _activity_from_finish(second).state == "cancelled"
        assert "status=failed business_status=cancelled" in caplog.text
    finally:
        runner.close()
