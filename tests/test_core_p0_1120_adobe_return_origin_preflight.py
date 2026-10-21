"""P0 #1120: offline Adobe-first return preflight never authorizes mutation.

These cases cover exact public responses and real locked Core orchestration
against synthetic SQLite state. No real Adobe/Paris/Kobo calls are made.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.core import return_adept_origin_preflight as origin
from cifereca.core.return_adept_orchestrator import (
    _public_origin_status, adept_return_key,
)
from tests.test_core_0984_adept_first_paris_reconcile import setup


@pytest.mark.parametrize("raw_status,expected_status,expected_retry", [
    ("no_acquisition", "provider_action_available", "provider_action_explicit"),
    ("ready", "adept_return_binding_required", "resolve_identity"),
    ("profile_missing", "profile_missing", "resolve_identity"),
    ("ADEPT_TOKEN=https://secret.invalid/private", "adept_return_binding_required",
     "resolve_identity"),
    (False, "adept_return_binding_required", "resolve_identity"),
    ({"profile": "private"}, "adept_return_binding_required", "resolve_identity"),
])
def test_missing_acquisition_preserves_only_closed_public_origin_codes(
    raw_status, expected_status, expected_retry,
):
    source = {"status": raw_status, "private": "PRIVATE_LICENSE_XML"}
    before = dict(source)
    result = origin.missing_acquisition_outcome(
        source, public_origin_status=_public_origin_status,
    )
    assert result == {
        "status": expected_status, "scope": "adobe_first_paris",
        "retry": expected_retry,
    }
    assert source == before
    assert "PRIVATE_LICENSE_XML" not in repr(result)


@pytest.mark.parametrize("status,available,expected_status", [
    ("ready", True, None),
    ("ready", False, "adept_return_state_required"),
    ("ready", 1, "adept_return_state_required"),
    ("ready", "true", "adept_return_state_required"),
    ("ready", None, "adept_return_state_required"),
    ("expired", True, "expired"),
    ("inventory_error", True, "inventory_error"),
    ("https://secret.invalid/private", True, "adept_return_state_required"),
    (None, True, "adept_return_state_required"),
])
def test_initial_return_claim_requires_closed_ready_and_native_true(
    status, available, expected_status,
):
    source = {"status": status, "return_available": available,
              "private": "PRIVATE_PROFILE"}
    before = dict(source)
    result = origin.unready_to_return_outcome(
        source, public_origin_status=_public_origin_status,
    )
    assert source == before
    if expected_status is None:
        assert result is None
    else:
        assert result == {
            "status": expected_status, "scope": "adobe_first_paris",
            "retry": "verification_only",
        }
        assert "PRIVATE_PROFILE" not in repr(result)


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
        result = service.return_loan_prefer_adept(loan)
        assert result == {
            "status": expected_status, "scope": "adobe_first_paris",
            "retry": expected_retry,
        }
        assert service.storage.loan_return_state(key) is None
        assert paris.posts == 0
    finally:
        service.close()


def test_refusal_helpers_are_called_before_claim_and_have_no_mutations():
    path = (Path(__file__).resolve().parents[1] /
            "src/cifereca/core/return_adept_orchestrator.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    handler = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "return_loan_prefer_adept")
    names = {ast.unparse(n.func): n.lineno
             for n in ast.walk(handler) if isinstance(n, ast.Call)
             and ast.unparse(n.func) in {
                 "return_adept_origin_preflight.missing_acquisition_outcome",
                 "return_adept_origin_preflight.unready_to_return_outcome",
                 "self.storage.loan_return_claim",
                 "self.return_adept_acquisition",
             }}
    assert (names["return_adept_origin_preflight.missing_acquisition_outcome"]
            < names["return_adept_origin_preflight.unready_to_return_outcome"]
            < names["self.storage.loan_return_claim"]
            < names["self.return_adept_acquisition"])
    helper_path = path.parent / "return_adept_origin_preflight.py"
    helper_tree = ast.parse(helper_path.read_text(encoding="utf-8"))
    calls = {ast.unparse(n.func) for n in ast.walk(helper_tree)
             if isinstance(n, ast.Call)}
    assert calls == {"public_origin_status", "source.get"}
