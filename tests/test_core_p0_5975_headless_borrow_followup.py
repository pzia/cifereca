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
    class Facade:
        @property
        def current_identity(self):
            seen.append("selected")
            return NS(identity_id="alice" if len(seen) == 1 else "bob")

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
    assert LibraryService.verify_manual_borrow_followup(host, object())["status"] == "blocked"
    assert seen == ["selected", "selected"]
    assert calls == ["alice", "bob"]


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
