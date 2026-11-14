"""#5975: read-only spent-borrow checks never infer GTK selection in Core."""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace as NS

from cifereca.core import borrow_later_verification as worker
from cifereca.services import LibraryService


def test_headless_spent_claim_missing_scope_refuses_before_any_lookup():
    class Host:
        @property
        def current_identity(self):
            raise AssertionError("Core read GTK selection")

        def __getattr__(self, key):
            raise AssertionError(f"unexpected provider/storage operation: {key}")

    host = Host()
    assert worker.borrow_followup_available(
        host, object(), readiness=lambda _: None,
    ) is False
    assert worker.borrow_followup_available(
        host, object(), readiness=lambda _: None, identity_id="alice",
    ) is False
    assert worker.verify_pending_borrow_followup(
        host, object(), readiness=lambda _: None,
    ) == {
        "status": "blocked", "reason_code": "borrow_verification_claim_mismatch",
        "retry_borrow": False, "mutated": False,
    }


def test_facade_pins_one_profile_once_for_read_and_verification(monkeypatch):
    seen = []
    alice = NS(identity_id="alice")
    bob = NS(identity_id="bob")
    class Facade:
        _account_context_epoch = 0
        selected = alice

        @property
        def current_identity(self):
            seen.append("selected")
            return self.selected

        def manual_borrow_diagnostic(self, item):
            raise AssertionError("readiness should not be invoked in stub")

    from cifereca.core import borrow_later_verification as module
    calls = []
    monkeypatch.setattr(module, "borrow_followup_available", lambda service, item, *,
                        readiness, identity_id: calls.append(identity_id) or False)
    monkeypatch.setattr(module, "verify_pending_borrow_followup", lambda service, item, *,
                        readiness, identity_id: calls.append(identity_id) or {
                            "status": "blocked", "reason_code": "borrow_verification_claim_mismatch",
                            "retry_borrow": False, "mutated": False,
                        })
    host = Facade()
    assert LibraryService.manual_borrow_followup_available(host, object()) is False
    host.selected = bob
    host._account_context_epoch += 1
    assert LibraryService.verify_manual_borrow_followup(host, object())["status"] == "blocked"
    # The unavailable read short-circuits before the post-check, while the
    # verification result is always checked once after its worker completes.
    assert seen == ["selected"] * 3
    assert calls == ["alice", "bob"]


def test_facade_blocks_positive_result_on_profile_switch_and_aba_epoch(monkeypatch):
    from cifereca.core import borrow_later_verification as module
    alice = NS(identity_id="alice")
    bob = NS(identity_id="bob")

    class Facade:
        selected = alice
        _account_context_epoch = 1

        @property
        def current_identity(self):
            return self.selected

        def manual_borrow_diagnostic(self, item):
            raise AssertionError("no diagnostic should be needed")

    host = Facade()

    def switch(_service, _item, *, readiness, identity_id):
        assert identity_id == "alice"
        host.selected = bob
        host.selected = alice
        host._account_context_epoch += 2
        return True

    monkeypatch.setattr(module, "borrow_followup_available", switch)
    assert LibraryService.manual_borrow_followup_available(host, object()) is False

    def switched_verification(_service, _item, *, readiness, identity_id):
        host._account_context_epoch += 2
        return {
            "status": "observed_current_loan",
            "reason_code": "borrow_verification_current_loan_observed",
            "retry_borrow": False, "mutated": False,
        }

    monkeypatch.setattr(module, "verify_pending_borrow_followup", switched_verification)
    result = LibraryService.verify_manual_borrow_followup(host, object())
    assert result["status"] == "blocked"
    assert result["retry_borrow"] is False and result["mutated"] is False


def test_headless_spent_claim_worker_has_no_navigation_access():
    for name in (
        "_pending_borrow_followup_scope", "borrow_followup_available",
        "verify_pending_borrow_followup",
    ):
        tree = ast.parse(inspect.getsource(getattr(worker, name)))
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "current_identity"
            for node in ast.walk(tree)
        )
        assert "current_identity" not in inspect.getsource(getattr(worker, name))
