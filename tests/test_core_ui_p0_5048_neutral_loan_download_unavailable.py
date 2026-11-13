"""P0 #5048: read-only loan refusal descriptions are not provider decisions."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.loan_download_status import loan_download_unavailable_message
from cifereca.services import LibraryService


@pytest.mark.parametrize("provider", ["bnf-pret", "paris", "synthetic-provider", "unknown"])
@pytest.mark.parametrize("connected,download_capability,expected", [
    (False, True, "provider_disconnected"),
    (True, False, "download_unsupported"),
])
def test_unavailable_digital_loan_has_same_generic_safe_reason(
    provider, connected, download_capability, expected,
):
    calls = []
    session = NS(connected=connected, capabilities=NS(digital_download=download_capability))
    class Service:
        def _session_for_source(self, actual_provider, credential):
            calls.append((actual_provider, credential))
            return session

    loan = NS(medium="digital", source_provider=provider, source_credential="credential")
    reason = LibraryService.loan_download_eligibility(Service(), loan)
    assert reason == {"available": False, "code": expected}
    message = loan_download_unavailable_message(reason)
    assert type(message) is str and message
    assert calls == [(provider, "credential")]
    assert "credential" not in message.lower()


def test_detail_panel_refusal_path_has_no_supplier_specific_exception():
    tree = ast.parse((Path(__file__).resolve().parents[1]
                      / "src/cifereca/gui/detail_panel.py").read_text(encoding="utf-8"))
    panel = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "DetailPanel")
    method = next(node for node in panel.body if isinstance(node, ast.FunctionDef)
                  and node.name == "show_loan")
    refusal = next(node for node in method.body
                   if isinstance(node, ast.If)
                   and "loan.medium" in ast.unparse(node.test)
                   and "not downloadable" in ast.unparse(node.test))
    assert ast.unparse(refusal.test) == "loan.medium == 'digital' and (not downloadable)"
    assert "loan_download_unavailable_message" in ast.unparse(refusal)
    # Explicit ACSM action remains separately guarded by its existing branch.
    assert "prepare_explicit_loan_acsm_for_loan" in ast.unparse(method)
