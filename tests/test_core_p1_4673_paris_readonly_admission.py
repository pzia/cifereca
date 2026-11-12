"""#4673: one provider-neutral local manual-borrow diagnostic, never rights.

Core/SQLite and synthetic Paris transport; no GET or mutating POST.
Provider details are supplied only by the installed typed plugin policy.
"""
from dataclasses import replace
from pathlib import Path
import ast

import pytest

from tests.test_paris_manual_borrow_dispatch import setup


def test_local_diagnostic_is_not_provider_permission(tmp_path):
    service, _session, _account, item, state = setup(tmp_path)
    try:
        diagnosis = service.manual_borrow_diagnostic(item)
        assert diagnosis == {
            "local_action_possible": True,
            "reason_code": "local_action_available_provider_unknown",
            "provider_eligibility": "unknown",
        }
        assert service.can_borrow_manual(item) is True
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        service.close()


@pytest.mark.parametrize("change", [
    {"source_provider": "bnf-pret"},
    {"docbase": "CATALOGUE"},
    {"medium": "physical"},
    {"source_credential": ""},
    {"metadata": {"catalogue_access": "shared_read_only"}},
])
def test_unqualified_edition_never_reaches_lender(tmp_path, change):
    service, _session, _account, item, state = setup(tmp_path)
    try:
        result = service.manual_borrow_diagnostic(replace(item, **change))
        assert result["local_action_possible"] is False
        assert result["provider_eligibility"] == "unknown"
        assert result["reason_code"] in {
            "edition_or_source_unqualified", "local_admission_unavailable",
        }
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        service.close()


def test_missing_identity_or_primary_source_is_closed(tmp_path):
    service, session, account, item, state = setup(tmp_path)
    try:
        service.current_identity = None
        diagnosis = service.manual_borrow_diagnostic(item)
        assert diagnosis["local_action_possible"] is False
        assert diagnosis["provider_eligibility"] == "unknown"
        service.current_identity = service.identity_by_id("default")
        account.unique_id = "linked-reader"
        diagnosis = service.manual_borrow_diagnostic(item)
        assert diagnosis["local_action_possible"] is False
        assert diagnosis["provider_eligibility"] == "unknown"
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        service.close()


def test_missing_installed_policy_fails_closed_and_never_leaks_private_data(tmp_path):
    service, session, account, item, state = setup(tmp_path)
    try:
        session.connected = False
        diagnosis = service.manual_borrow_diagnostic(item)
        assert diagnosis == {
            "local_action_possible": False,
            "reason_code": "edition_or_source_unqualified",
            "provider_eligibility": "unknown",
        }
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        service.close()


def test_paris_diagnostic_facade_is_physically_retired():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "src/cifereca/services.py").read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "LibraryService")
    methods = {node.name for node in owner.body if isinstance(node, ast.FunctionDef)}
    assert "paris_borrow_diagnostic" not in methods
    assert "manual_borrow_diagnostic" in methods
    assert "manual_borrow_followup_available" in methods
    assert "verify_manual_borrow_followup" in methods
