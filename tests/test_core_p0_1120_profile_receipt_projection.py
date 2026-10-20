"""#1120: pure refusal receipt projection, exact historic field order.

No ACSM, profile resolution, provider, persistent write or network access.
The caller's existing profile guards still control when storage is written.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.core.loan_adept_profile_receipt import attention_receipt


@pytest.mark.parametrize("branch,message,attempt,include_profile", [
    ("borrower", None, None, False),
    ("unresolved", "Profil ADEPT indisponible", None, False),
    ("unready", "Profil ADEPT non prêt", "private-reader", True),
])
def test_exact_legacy_receipt_shape_and_key_order(
    branch, message, attempt, include_profile,
):
    ledger = {"credential_id": "private-credential", "identity_id": "private-id"}
    previous = {"cached_phase": "previous", "provider_loan_id": "historical"}
    ledger_copy, previous_copy = dict(ledger), dict(previous)
    receipt = attention_receipt(
        ledger_base=ledger, previous_meta=previous,
        loan_id="private-loan", retained_acsm=Path("/synthetic/private.acsm"),
        code="adept_profile_required",
        error_message=message, attempt_profile=attempt,
        include_attempt_profile=include_profile,
    )
    metadata = {
        **previous, "provider_loan_id": "private-loan",
        "acsm_staging_path": "/synthetic/private.acsm",
    }
    if include_profile:
        metadata["adept_attempt_profile"] = "private-reader"
    expected = {
        **ledger, "status": "attention_required", "strategy": "loan_auto",
        "executor": "kobo-adept", "attention_code": "adept_profile_required",
    }
    if message is not None:
        expected["error_message"] = message
    expected["metadata"] = metadata
    assert receipt == expected
    assert list(receipt) == list(expected)
    assert list(receipt["metadata"]) == list(metadata)
    assert ledger == ledger_copy and previous == previous_copy
    assert receipt["metadata"] is not previous


def test_profile_refusals_keep_single_persistence_in_each_existing_branch():
    path = (Path(__file__).resolve().parents[1] /
            "src/cifereca/core/loan_adept_profile_guard.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    check = next(n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "check")
    writes = [
        node for node in ast.walk(check) if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "self.storage.acquisition_put"
    ]
    assert len(writes) == 3
    for write in writes:
        assert len(write.args) == 1
        assert isinstance(write.args[0], ast.Call)
        assert ast.unparse(write.args[0].func) == "attention_receipt"
    source = path.read_text(encoding="utf-8")
    assert source.index("borrower_code is not None") < source.index(
        "resolve_adept_profile_for_identity")
    assert source.index("profile_is_explicitly_acsm_unready") > source.index(
        "resolve_adept_profile_for_identity")


def test_projection_module_cannot_write_or_call_provider():
    import cifereca.core.loan_adept_profile_receipt as module
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    projections = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert len(projections) == 1 and projections[0].name == "attention_receipt"
    callees = {ast.unparse(n.func) for n in ast.walk(projections[0])
               if isinstance(n, ast.Call)}
    assert callees == {"str"}
