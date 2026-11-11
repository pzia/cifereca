"""P0 #1120: offline Adobe-first return preflight never authorizes mutation.

These cases cover exact public responses and real locked Core orchestration
against synthetic SQLite state. No real Adobe/Paris/Kobo calls are made.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.core.return_adept_orchestrator import (
    adept_return_key,
)
from tests.test_core_0984_adept_first_paris_reconcile import setup


@pytest.mark.parametrize("source,expected_status,expected_retry", [
    ({"status": "ready", "return_available": True},
     "adept_return_binding_required", "resolve_identity"),
    ({"status": "no_acquisition"},
     "provider_action_available", "provider_action_explicit"),
    ({"status": "ready", "acquisition_id": "adept-01", "return_available": False},
     "adept_return_state_required", "verification_only"),
    ({"status": "ready", "acquisition_id": "adept-01", "return_available": 1},
     "adept_return_state_required", "verification_only"),
    ({"status": "inventory_error", "acquisition_id": "adept-01",
      "return_available": True},
     "inventory_error", "verification_only"),
])
def test_real_core_facade_refuses_before_checkpoint_or_adobe(
    tmp_path, source, expected_status, expected_retry,
):
    service, loan, paris = setup(tmp_path, paris_active=True)
    try:
        service.adept_return_status_for_loan = lambda _: dict(source)
        service.return_adept_acquisition = lambda *a, **k: pytest.fail(
            "preflight must never invoke Adobe"
        )
        service.verify_paris_return_inventory = lambda *a, **k: pytest.fail(
            "preflight must never check Paris"
        )
        key = adept_return_key(
            loan.source_provider, loan.source_credential, "linked-123", loan.id,
        )
        assert service.storage.loan_return_state(key) is None
        with pytest.raises(Exception) as failure:
            service.return_loan_prefer_adept(loan)
        assert getattr(failure.value, "code", None) == "loan_return_removed"
        assert service.storage.loan_return_state(key) is None
        assert paris.posts == 0
    finally:
        service.close()


def test_legacy_orchestrator_is_inert_and_preflight_helpers_removed():
    path = (Path(__file__).resolve().parents[1] /
            "src/cifereca/core/return_adept_orchestrator.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "return_loan_prefer_adept"
        for node in tree.body
    )

    # No obsolete preflight that could be misused to infer return permission.
    assert not (path.parent / "return_adept_origin_preflight.py").exists()
