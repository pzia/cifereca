"""#4673: source of truth for read-only Paris admission refusal phases.

Real Core/SQLite + synthetic Paris session; no supplier request or POST.
"""
from types import SimpleNamespace

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core import manual_library_borrow
from tests.test_paris_manual_borrow_dispatch import setup


@pytest.mark.parametrize(("mode", "reason"), [
    ("wrong_primary_account", "local_primary_account_unverified"),
    ("disconnected", "local_session_unavailable"),
    ("previous_claim", "local_previous_attempt"),
    ("capability_unavailable", "local_borrow_capability_unavailable"),
    ("executor_unavailable", "local_borrow_executor_unavailable"),
])
def test_diagnostic_phase_explains_exact_local_refusal(
    tmp_path, monkeypatch, mode, reason,
):
    service, session, account, item, state = setup(tmp_path)
    try:
        if mode == "wrong_primary_account":
            service.accounts = []
        elif mode == "disconnected":
            session.connected = False
        elif mode == "previous_claim":
            key = borrow_claim_key(account, item)
            service.storage.metadata_set(key, "invalid-but-spent-checkpoint")
        elif mode == "capability_unavailable":
            monkeypatch.setattr(
                manual_library_borrow, "assess_lending_operation",
                lambda *a, **kw: SimpleNamespace(usable=False),
            )
        elif mode == "executor_unavailable":
            monkeypatch.setattr(
                manual_library_borrow, "getattr_static",
                lambda *a, **kw: None,
            )
        diagnosis = service.paris_borrow_diagnostic(item)
        assert diagnosis == {
            "local_action_possible": False,
            "reason_code": reason,
            "provider_eligibility": "unknown",
        }
        assert service.can_borrow_paris(item) is False
        assert state["mutations"] == 0
        assert state["calls"] == []
    finally:
        service.close()


def test_success_and_empty_diagnostic_have_same_non_authorizing_boolean(tmp_path):
    service, session, account, item, state = setup(tmp_path)
    try:
        raw = {"not_a_public_category": "should be removed"}
        assert manual_library_borrow.manual_borrow_available(
            service, item, provider_id="paris", docbase="DILICOM",
            primary_id="", diagnostic=raw,
        ) is True
        assert raw == {"reason_code": "local_action_available_provider_unknown"}
        assert service.paris_borrow_diagnostic(item)["local_action_possible"] is True
        assert service.can_borrow_paris(item)
        assert state["mutations"] == 0 and state["calls"] == []
    finally:
        service.close()


def test_paris_readiness_uses_one_shared_local_admission():
    """No second provider-specific decision path in the boolean facade."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "src/cifereca/services.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    library = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef)
                   and node.name == "LibraryService")
    method = next(node for node in library.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "can_borrow_paris")
    calls = [
        node for node in ast.walk(method)
        if isinstance(node, ast.Call)
    ]
    assert len(calls) == 1
    assert ast.unparse(calls[0].func) == "self.paris_borrow_diagnostic"
    assert not any(
        isinstance(node, ast.Constant) and node.value in {
            "paris", "DILICOM", "primary_paris_connection_id"
        }
        for node in ast.walk(method)
    )
