"""#5055: provider-neutral service facade admits only exact plugin ABI.

No BnF dispatch in the staging facade: selected provider supplies the
opaque branch and closed diagnostic codes, Core owns consent and lease.
"""
from pathlib import Path
import ast


def test_staging_service_facade_has_no_bnf_dispatch():
    source = (Path(__file__).resolve().parents[1] / "src/cifereca/services.py").read_text()
    tree = ast.parse(source)
    service = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "LibraryService")
    stage = next(node for node in service.body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "stage_explicit_loan_acsm")
    body = ast.unparse(stage)
    assert "stage_explicit_loan_acsm(" in body
    assert "loan_acsm_stage_profile" in body
    assert "bnf-pret" not in body
    assert "bnf_" not in body
    assert all(not (isinstance(node, ast.FunctionDef)
                    and node.name == "stage_bnf_loan_acsm") for node in service.body)
