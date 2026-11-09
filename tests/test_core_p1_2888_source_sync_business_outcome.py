"""#2888: typed business failures are not TaskRunner success receipts.

Uses the production TaskRunner class with the established headless GLib
adapter; no provider, GTK session or network is involved.
"""
import logging
from types import MappingProxyType

import pytest

from cifereca.plugin_api import SyncResult
from tests.test_core_p0_981_redacted_task_traces import _runner


@pytest.mark.parametrize(("source_result", "message"), [
    (SyncResult(ok=False, facts={"reason": "operation_busy"}),
     "Synchronisation déjà en cours"),
    (SyncResult(ok=False, facts=MappingProxyType({"reason": "operation_busy"})),
     "Synchronisation déjà en cours"),
    (SyncResult(ok=False, facts={"status": "partial"}),
     "Synchronisation partielle"),
    (SyncResult(ok=False, facts={"status": "failed"}),
     "Échec de synchronisation"),
    (SyncResult(ok=False, facts={"sources": {"opaque": {"status": "operation_busy"}}}),
     "Synchronisation déjà en cours"),
    (SyncResult(ok=False, facts=MappingProxyType({
        "sources": MappingProxyType({
            "opaque": MappingProxyType({"status": "operation_busy"}),
        }),
    })), "Synchronisation déjà en cours"),
    (SyncResult(ok=False, facts={"sources": {"opaque": {"status": "partial"}}}),
     "Synchronisation partielle"),
    (SyncResult(ok=False, facts={"sources": {"a": {"status": "partial"},
                                           "b": {"status": "failed"}}}),
     "Échec de synchronisation"),
])
def test_source_sync_business_failure_is_a_failed_activity_not_a_worker_exception(
    caplog, source_result, message,
):
    runner, displayed, flush, logger = _runner()
    delivered, exceptions = [], []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Synchronisation privée", lambda: source_result,
                ok=delivered.append, err=exceptions.append,
                kind="source-sync", origin="user",
                success_toast="Succès mensonger",
            )
            flush()
        assert delivered == [source_result]  # UI needs refresh, even on partial.
        assert exceptions == []              # Business refusal isn't exception.
        finishes = [args for name, args in displayed if name == "finished"]
        assert len(finishes) == 1
        assert finishes[0][3] is False
        assert finishes[0][4] == message
        assert "kind=source-sync" in caplog.text
        assert "status=failed" in caplog.text
        assert "status=success" not in caplog.text
    finally:
        runner.close()


def test_successful_source_sync_and_legacy_worker_keep_existing_callback_semantics(caplog):
    runner, displayed, flush, logger = _runner()
    delivered = []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Sync positive", lambda: SyncResult(ok=True, changed=2),
                ok=delivered.append, kind="source-sync",
            )
            flush()
        assert delivered == [SyncResult(ok=True, changed=2)]
        assert [args[3] for name, args in displayed if name == "finished"] == [True]
        assert "status=success" in caplog.text

        # Only the typed sync surface may change the legacy runner contract.
        displayed.clear()
        runner.submit(
            "Legacy unrelated task", lambda: SyncResult(ok=False),
            ok=delivered.append, kind="library",
        )
        flush()
        assert [args[3] for name, args in displayed if name == "finished"] == [True]
    finally:
        runner.close()


def test_source_sync_exception_still_dispatches_error_callback():
    runner, displayed, flush, _logger = _runner()
    errors = []
    def failure():
        raise RuntimeError("network error")
    try:
        runner.submit("Sync exception", failure, err=errors.append, kind="source-sync")
        flush()
        assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
        assert [args[3] for name, args in displayed if name == "finished"] == [False]
    finally:
        runner.close()


def test_real_executor_correlates_typed_business_refusal_without_exception(caplog):
    from cifereca.diagnostics import Diagnostics
    from cifereca.task_queue import PriorityExecutor
    diag = Diagnostics()
    pool = PriorityExecutor(max_workers=1)
    expected = SyncResult(ok=False, facts={"reason": "operation_busy"})

    def refused():
        diag.add("NUMILOG", "acquisition_delivery", "error", detail={
            "phase": "completed", "code": "operation_busy",
        })
        return expected

    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            assert pool.submit(0, refused).result(timeout=3) is expected
        ref = diag.snapshot()[0]["task_ref"]
        assert f"task_ref={ref} status=failed" in caplog.text
        assert f"task_ref={ref} status=success" not in caplog.text
    finally:
        pool.shutdown(wait=True)


def test_real_executor_keeps_typed_positive_as_success_and_no_phantom_ref(caplog):
    from cifereca.diagnostics import Diagnostics
    from cifereca.task_queue import PriorityExecutor
    diag = Diagnostics()
    pool = PriorityExecutor(max_workers=1)
    try:
        def success():
            diag.add("NUMILOG", "acquisition_delivery", "ok", detail={
                "phase": "completed", "code": "complete",
            })
            return SyncResult(ok=True)
        with caplog.at_level(logging.INFO, logger="cifereca"):
            assert pool.submit(0, success).result(timeout=3).ok is True
        ref = diag.snapshot()[0]["task_ref"]
        assert f"task_ref={ref} status=success" in caplog.text
        caplog.clear()
        assert pool.submit(0, lambda: SyncResult(ok=False)).result(timeout=3).ok is False
        assert "TRACE task correlation" not in caplog.text
    finally:
        pool.shutdown(wait=True)



@pytest.mark.parametrize(("facts", "expected"), [
    ({"reason": "operation_busy"}, "busy"),
    ({"status": "partial"}, "partial"),
    ({"status": "failed"}, "failed"),
    ({"sources": {"opaque": {"status": "operation_busy"}}}, "busy"),
])
def test_task_trace_keeps_failure_receipt_but_distinguishes_source_business_code(
    caplog, facts, expected,
):
    """#2888: analysis can distinguish contention, partial and failed runs."""
    runner, displayed, flush, logger = _runner()
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Source privée", lambda: SyncResult(ok=False, facts=facts),
                kind="source-sync", origin="automatic",
            )
            flush()
        terminal = [
            record.getMessage() for record in caplog.records
            if "TRACE task outcome id=" in record.getMessage()
        ]
        assert len(terminal) == 1
        assert "status=failed" in terminal[0]
        assert f"business_status={expected}" in terminal[0]
        assert [args[3] for name, args in displayed if name == "finished"] == [False]
    finally:
        runner.close()


def test_task_trace_business_code_is_closed_and_source_sync_only(caplog):
    runner, displayed, flush, logger = _runner()
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Sync réussite", lambda: SyncResult(ok=True),
                kind="source-sync", origin="user",
            )
            flush()
            runner.submit(
                "Autre tâche", lambda: {"status": "private", "token": "SECRET_VALUE"},
                kind="library", origin="user",
            )
            flush()
        terminals = [
            record.getMessage() for record in caplog.records
            if "TRACE task outcome id=" in record.getMessage()
        ]
        assert len(terminals) == 2
        assert "status=success business_status=success" in terminals[0]
        assert "business_status=" not in terminals[1]
        assert "SECRET_VALUE" not in " ".join(terminals)
    finally:
        runner.close()


@pytest.mark.parametrize(("result", "expected"), [
    ({"status": "verification_unavailable", "reason_code": "borrow_result_unverified"},
     "Demande d’emprunt envoyée ; confirmation indisponible. Ne réempruntez pas : actualisez vos prêts ultérieurement."),
    ({"status": "verification_unavailable", "reason_code": "borrow_checkpoint_unavailable"},
     "Emprunt non confirmé — vérification nécessaire"),
    ({"status": "blocked", "reason_code": "borrow_not_submitted"},
     "Emprunt bloqué — aucune confirmation"),
])
def test_uncertain_borrow_receipt_does_not_invite_a_second_mutation(result, expected):
    runner, displayed, flush, _logger = _runner()
    try:
        runner.submit("Emprunt", lambda: result, kind="borrow", origin="user")
        flush()
        finish = [args for name, args in displayed if name == "finished"]
        assert len(finish) == 1
        assert finish[0][3] is False
        assert finish[0][4] == expected
    finally:
        runner.close()
