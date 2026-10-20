"""#1120: exact failure ledger projections under the original guarded order.

Only pure dictionaries are inspected here: no ACSM, Adobe, Paris, network,
storage write, retry decision, profile selection or filesystem side effect.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.core import loan_failure_evidence


@pytest.mark.parametrize("status,expected_attention", [
    ("attention_required", "adept_license_refused"),
    ("retryable_error", None),
])
def test_adept_failure_receipt_exact_historical_shape(status, expected_attention):
    ledger = {"provider_id": "paris", "credential_id": "private-credential",
              "item_id": "private-item", "identity_id": "private-identity"}
    metadata = {"provider_loan_id": "private-loan", "process": {"returncode": 10},
                "acsm_staging_path": "/fictional/private.acsm"}
    original_ledger, original_meta = dict(ledger), dict(metadata)
    receipt = loan_failure_evidence.adept_failure_receipt(
        ledger_base=ledger, status=status, executor="kobo-adept",
        code="adept_error", message="Acquisition ADEPT indisponible",
        metadata=metadata, attention_code="adept_license_refused",
    )
    assert receipt == {
        **ledger, "status": status, "strategy": "loan_auto",
        "executor": "kobo-adept", "error_code": "adept_error",
        "error_message": "Acquisition ADEPT indisponible",
        "metadata": metadata, "attention_code": expected_attention,
    }
    assert receipt["metadata"] is metadata
    assert ledger == original_ledger and metadata == original_meta


@pytest.mark.parametrize("validation_failure,expected_code", [
    (False, "provider_acquisition_failed"),
    (True, "artifact_invalid"),
])
def test_provider_failure_receipt_remains_retryable_without_adept_inference(
    validation_failure, expected_code,
):
    ledger = {"provider_id": "paris", "item_id": "private-book"}
    metadata = {"provider_loan_id": "private-loan", "repairable": validation_failure}
    receipt = loan_failure_evidence.provider_failure_receipt(
        ledger_base=ledger, executor="kobo-adept",
        validation_failure=validation_failure, metadata=metadata,
    )
    assert receipt == {
        **ledger, "status": "retryable_error", "strategy": "loan_auto",
        "executor": "kobo-adept", "error_code": expected_code,
        "error_message": "Acquisition du prêt indisponible",
        "attention_code": None, "metadata": metadata,
    }
    assert receipt["metadata"] is metadata
    assert "status" not in ledger and "error_code" not in ledger


def _service_acquisition_tree():
    source = Path(__file__).resolve().parents[1] / "src/cifereca/services.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    service = next(node for node in module.body
                   if isinstance(node, ast.ClassDef) and node.name == "LibraryService")
    return next(node for node in service.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_acquire_active_loan_automatically_guarded")


def test_both_guarded_failure_branches_persist_once_before_response():
    method = _service_acquisition_tree()
    for receipt, outcome in (
        ("adept_failure_receipt", "adept_failure_outcome"),
        ("provider_failure_receipt", "provider_failure_outcome"),
    ):
        handlers = [
            node for node in ast.walk(method)
            if isinstance(node, ast.ExceptHandler)
            and any(
                isinstance(call, ast.Call) and
                ast.unparse(call.func) == f"loan_failure_evidence.{outcome}"
                for call in ast.walk(node)
            )
        ]
        assert len(handlers) == 1
        calls = [
            (ast.unparse(node.func), node.lineno, node)
            for node in ast.walk(handlers[0]) if isinstance(node, ast.Call)
        ]
        placed = [(line, node) for name, line, node in calls
                  if name == "self.storage.acquisition_put"]
        projected = [(line, node) for name, line, node in calls
                     if name == f"loan_failure_evidence.{receipt}"]
        results = [line for name, line, _ in calls
                   if name == f"loan_failure_evidence.{outcome}"]
        assert len(placed) == len(projected) == len(results) == 1
        assert placed[0][0] <= projected[0][0] < results[0]
        assert len(placed[0][1].args) == 1
        assert isinstance(placed[0][1].args[0], ast.Call)
        assert ast.unparse(placed[0][1].args[0].func) == (
            f"loan_failure_evidence.{receipt}"
        )


def test_failure_receipt_helpers_have_no_calls_to_services_or_providers():
    module = ast.parse(
        Path(loan_failure_evidence.__file__).read_text(encoding="utf-8"),
    )
    for name in ("adept_failure_receipt", "provider_failure_receipt"):
        definition = next(n for n in module.body
                          if isinstance(n, ast.FunctionDef) and n.name == name)
        assert not [n for n in ast.walk(definition) if isinstance(n, ast.Call)]
