"""P0 #1120: failure response extraction preserves the guarded workflow API.

These projections are intentionally not a new classifier: upstream guards,
exception handling, error sanitization and ledger writes remain in services.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.core.loan_failure_evidence import (
    adept_failure_outcome, provider_failure_outcome,
)


@pytest.mark.parametrize("status,expected_code", [
    ("attention_required", "manual_review"),
    ("retryable_error", "kobo_adept_invalid_json"),
])
def test_classified_adept_failure_preserves_existing_mapping_and_process(
    status, expected_code,
):
    process = {"returncode": 3, "duration_ms": 20}
    before = dict(process)
    outcome = adept_failure_outcome(
        loan_id="private-loan-id", status=status,
        message="Acquisition ADEPT indisponible",
        attention_code="manual_review", code="kobo_adept_invalid_json",
        process=process,
    )
    assert outcome == {
        "status": status,
        "loan_id": "private-loan-id",
        "error": "Acquisition ADEPT indisponible",
        "code": expected_code,
        "process": process,
    }
    assert outcome["process"] is process and process == before


@pytest.mark.parametrize("attention_code", [None, "provider_quota", "manual_review"])
def test_provider_failure_is_still_retryable_with_same_user_message(attention_code):
    result = provider_failure_outcome(
        loan_id="private-loan-id", attention_code=attention_code,
    )
    assert result == {
        "status": "retryable_error",
        "loan_id": "private-loan-id",
        "code": attention_code,
        "error": "Acquisition du prêt indisponible",
    }


def test_response_projection_does_not_write_ledger_or_follow_licence():
    from cifereca.core import loan_failure_evidence
    source = Path(loan_failure_evidence.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for fn in ("adept_failure_outcome", "provider_failure_outcome"):
        definition = next(node for node in tree.body
                          if isinstance(node, ast.FunctionDef) and node.name == fn)
        calls = [ast.unparse(node.func) for node in ast.walk(definition)
                 if isinstance(node, ast.Call)]
        assert calls == []  # No service, disk, Adobe or provider access.


def test_service_keeps_error_receipt_before_callers_failure_response():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/services.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    service = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "LibraryService")
    method = next(node for node in service.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_acquire_active_loan_automatically_guarded")
    for expected in (
        "loan_failure_evidence.adept_failure_outcome",
        "loan_failure_evidence.provider_failure_outcome",
    ):
        branches = [
            node for node in ast.walk(method)
            if isinstance(node, ast.ExceptHandler)
            and any(isinstance(call, ast.Call)
                    and ast.unparse(call.func) == expected
                    for call in ast.walk(node))
        ]
        assert len(branches) == 1
        branch = branches[0]
        persisted = [
            node.lineno for node in ast.walk(branch)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "self.storage.acquisition_put"
        ]
        outcomes = [
            node.lineno for node in ast.walk(branch)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == expected
        ]
        assert len(persisted) == len(outcomes) == 1
        assert persisted[0] < outcomes[0]
