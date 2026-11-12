"""#4673: installed typed policy replaces the Paris-only readonly facade.

Real SQLite Core, offline synthetic Paris transport, no lender request/POST.
The generic diagnostic exposes closed local reasons, never borrower rights.
"""
from dataclasses import replace
from pathlib import Path
import ast

import pytest

from cifereca.core import manual_library_borrow
from tests.test_paris_manual_borrow_dispatch import setup


def _diagnose(service, item):
    return service.manual_borrow_diagnostic(item)


def test_positive_local_scope_never_claims_provider_permission(tmp_path):
    service, _session, account, item, state = setup(tmp_path)
    try:
        assert _diagnose(service, item) == {
            "local_action_possible": True,
            "reason_code": "local_action_available_provider_unknown",
            "provider_eligibility": "unknown",
        }
        assert service.can_borrow_manual(item) is True
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        service.close()


@pytest.mark.parametrize("change", [
    {"source_provider": "bnf-pret"}, {"docbase": "CATALOGUE"},
    {"medium": "physical"}, {"source_credential": ""},
])
def test_wrong_edition_or_source_denied_before_supplier_io(tmp_path, change):
    service, _session, account, item, state = setup(tmp_path)
    try:
        result = _diagnose(service, replace(item, **change))
        assert result == {
            "local_action_possible": False,
            "reason_code": "edition_or_source_unqualified",
            "provider_eligibility": "unknown",
        }
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        service.close()


def test_missing_identity_diagnostic_is_inert(tmp_path):
    service, _session, account, item, state = setup(tmp_path)
    try:
        service.current_identity = None
        result = _diagnose(service, item)
        assert result["local_action_possible"] is False
        assert result["reason_code"] == "identity_not_selected"
        assert result["provider_eligibility"] == "unknown"
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        service.close()


def test_primary_connection_mismatch_remains_closed_code(tmp_path):
    service, _session, account, item, state = setup(tmp_path)
    try:
        service.storage.provider_connection_put({
            "identity_id": "default", "provider_id": "paris",
            "credential_id": "card-A", "label": "disabled",
            "enabled": False, "metadata": {},
        })
        result = _diagnose(service, item)
        assert result["local_action_possible"] is False
        assert result["reason_code"] == "local_primary_source_binding_mismatch"
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        service.close()


def test_generic_diagnostic_never_leaks_exception_content(tmp_path, monkeypatch):
    service, _session, account, item, state = setup(tmp_path)
    try:
        def secret(*_a, **_kwargs):
            raise RuntimeError("PRIVATE_SIGNED_URL_AND_CARD")
        monkeypatch.setattr(manual_library_borrow, "manual_borrow_diagnostic", secret)
        result = _diagnose(service, item)
        assert result == {
            "local_action_possible": False,
            "reason_code": "local_admission_unavailable",
            "provider_eligibility": "unknown",
        }
        assert "PRIVATE_" not in repr(result)
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        service.close()


def test_paris_only_diagnostic_method_physically_deleted():
    source = (Path(__file__).resolve().parents[1]
              / "src/cifereca/services.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef)
                 and node.name == "LibraryService")
    methods = {node.name for node in owner.body
               if isinstance(node, ast.FunctionDef)}
    assert "paris_borrow_diagnostic" not in methods
    assert "manual_borrow_diagnostic" in methods
    assert "manual_borrow_followup_available" in methods
    assert "verify_manual_borrow_followup" in methods
