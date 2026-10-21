"""Final executable #1120 structural-closure gate.

Synthetic SQLite, fake Paris inventory, and fake ADEPT runtime ONLY. Nothing
here borrows/returns a real book or talks to a provider. The companion audit
records the remaining *unvalidated* real Adobe/Paris business journeys.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from cifereca.core.loan_return_guard import return_key
from cifereca.core.return_adept_orchestrator import adept_return_key
from cifereca.services import LibraryService
from tests.test_core_0984_adept_first_paris_reconcile import (
    setup as adobe_setup, stub_adept,
)
from tests.test_paris_borrower_adept_binding import (
    service_with_linked_borrower,
)


def _method(name):
    source = Path(inspect.getfile(LibraryService)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "LibraryService")
    return next(node for node in cls.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name)


def _calls(node):
    return [
        ast.unparse(item.func)
        for item in sorted(
            (child for child in ast.walk(node) if isinstance(child, ast.Call)),
            key=lambda child: (child.lineno, child.col_offset),
        )
    ]


def test_acquisition_facade_keeps_original_signature_and_cross_process_lock():
    method = LibraryService._acquire_active_loan_automatically
    sig = inspect.signature(method)
    assert list(sig.parameters) == ["self", "loan", "force_refresh"]
    assert sig.parameters["force_refresh"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["force_refresh"].default is False
    calls = _calls(_method("_acquire_active_loan_automatically"))
    assert "InterProcessLock" in calls
    assert "self._acquire_active_loan_automatically_guarded" in calls
    assert calls.index("InterProcessLock") < calls.index(
        "self._acquire_active_loan_automatically_guarded"
    )
    guarded = _method("_acquire_active_loan_automatically_guarded")
    assert ast.unparse(guarded.body[1]) == "force_refresh = force_refresh is True"


def test_acsm_stages_preserve_admission_provider_adept_index_receipt_order():
    calls = _calls(_method("_acquire_active_loan_automatically_guarded"))
    boundary = [
        "loan_acquisition_admission.resolve",
        "loan_local_preparation.prepare",
        "loan_initial_receipt.acquiring_receipt",
        "loan_provider_delivery.acquire_provider_loan",
        "loan_adept_profile_guard.check",
        "adept_acsm_plan.build_fulfillment_request",
        "loan_adept_fulfillment.fulfill",
        "adept_artifact_publication.publish",
        "loan_provider_delivery.index_local_loan_artifact",
        "loan_completion_receipt.completed_receipt",
        "self._cleanup_acsm_after_success",
    ]
    positions = [calls.index(name) for name in boundary]
    assert positions == sorted(positions)
    # The guarded facade owns the two durable commit points, never a pure
    # planning module. Both writes enclose the provider + Adobe pipeline.
    commits = [i for i, call in enumerate(calls)
               if call == "self.storage.acquisition_put"]
    assert commits[0] < calls.index("loan_provider_delivery.acquire_provider_loan")
    # The completed receipt is an *argument* of acquisition_put: AST
    # parent location precedes its child, but evaluation is child first.
    assert commits[1] < calls.index("loan_completion_receipt.completed_receipt")
    assert commits[1] < calls.index("self._cleanup_acsm_after_success")
    write_nodes = sorted(
        (node for node in ast.walk(_method("_acquire_active_loan_automatically_guarded"))
         if isinstance(node, ast.Call)
         and ast.unparse(node.func) == "self.storage.acquisition_put"),
        key=lambda node: (node.lineno, node.col_offset),
    )
    assert ast.unparse(write_nodes[0].args[0].func) == (
        "loan_initial_receipt.acquiring_receipt"
    )
    assert ast.unparse(write_nodes[1].args[0].func) == (
        "loan_completion_receipt.completed_receipt"
    )


def test_return_facades_keep_lock_and_distinct_mutation_seams():
    for name, delegated in (
        ("return_loan_prefer_adept", "self._return_loan_prefer_adept_guarded"),
        ("return_digital", "self._return_digital_guarded"),
    ):
        facade = _method(name)
        calls = _calls(facade)
        assert "InterProcessLock" in calls
        assert delegated in calls
        # The non-Paris return path intentionally bypasses the Paris lock.
        # Verify that the *Paris* delegated call remains inside its lock.
        guarded = [
            node for node in ast.walk(facade)
            if isinstance(node, ast.With) and any(
                isinstance(item.context_expr, ast.Call)
                and ast.unparse(item.context_expr.func) == "InterProcessLock"
                for item in node.items
            )
        ]
        assert len(guarded) == 1
        assert delegated in _calls(guarded[0])
    assert "return_adept_orchestrator.return_loan_prefer_adept" in _calls(
        _method("_return_loan_prefer_adept_guarded")
    )
    assert "provider_loan_return.return_digital" in _calls(
        _method("_return_digital_guarded")
    )
    assert "return_adept_orchestrator.return_loan_prefer_adept" not in _calls(
        _method("return_digital")
    )


def test_unbound_paris_borrower_fails_before_provider_and_ledger(tmp_path, monkeypatch):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        monkeypatch.setattr(
            service, "acquire_digital_loan",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("unbound borrower must not call Paris")
            ),
        )
        before = service.storage.acquisitions(provider_id="paris")
        outcome = service._acquire_active_loan_automatically(loan)
        assert outcome["status"] == "attention_required"
        assert outcome["retry"] == "explicit_only"
        assert service.storage.acquisitions(provider_id="paris") == before
    finally:
        service.close()


def test_adept_first_repeated_reads_and_nonboolean_retry_never_repost(tmp_path):
    service, loan, paris = adobe_setup(tmp_path, paris_active=True)
    try:
        counts = stub_adept(service)
        first = service.return_loan_prefer_adept(loan)
        assert first["status"] == "partial"
        assert first["adobe_return_confirmed"] is True
        assert counts["mutations"] == 1 and paris.posts == 0
        assert service.return_loan_prefer_adept(loan)["status"] == "partial"
        assert service.verify_loan_return(loan)["status"] == "partial"
        assert service.return_digital(loan)["reason"] == (
            "adobe_return_takes_precedence"
        )
        assert service.return_digital(loan, explicit_retry="true")["reason"] == (
            "adobe_return_takes_precedence"
        )
        assert counts["mutations"] == 1 and paris.posts == 0
        assert service.return_digital(loan, explicit_retry=True)["status"] == (
            "still_active"
        )
        assert counts["mutations"] == 1 and paris.posts == 1
        # A non-explicit second request prioritizes the Adobe checkpoint,
        # not the local PNB still-active status; no second POST is possible.
        repeat = service.return_digital(loan)
        assert repeat["status"] == "partial"
        assert repeat["reason"] == "adobe_return_takes_precedence"
        assert paris.posts == 1
    finally:
        service.close()


def test_failed_adept_mutation_checkpoint_survives_retry_without_pnb(tmp_path):
    service, loan, paris = adobe_setup(tmp_path, paris_active=True)
    try:
        counts = stub_adept(service, failure=True)
        first = service.return_loan_prefer_adept(loan)
        assert first["status"] == "failed"
        assert first["adobe_return_confirmed"] is False
        assert counts["mutations"] == 1 and paris.posts == 0
        assert service.return_loan_prefer_adept(loan)["status"] == "failed"
        assert service.verify_loan_return(loan)["status"] == "failed"
        assert counts["mutations"] == 1 and paris.posts == 0
        key = adept_return_key(
            loan.source_provider, loan.source_credential,
            loan.source_account_id, loan.id,
        )
        assert service.storage.loan_return_state(key)["status"] == "failed"
        assert service.storage.loan_return_state(return_key(
            loan.source_provider, loan.source_credential,
            loan.source_account_id, loan.id,
        )) is None
    finally:
        service.close()
