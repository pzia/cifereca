"""#5055: no supplier-specific service projection after BnF UI cutover."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_bnf_service_observer_not_reintroduced():
    service = (ROOT / "src/cifereca/services.py").read_text(encoding="utf-8")
    tree = ast.parse(service)
    methods = {
        node.name
        for cls in tree.body if isinstance(cls, ast.ClassDef)
        for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "bnf_borrow_claim_observation" not in methods
    # Borrow admission and one-shot protection must remain independent.
    assert "borrow_bnf_manual" not in methods
    assert "borrow_manual" in methods
    assert "can_borrow_bnf" not in methods
    assert "can_borrow_manual" in methods
    assert "manual_borrow_policy" in methods
    # The BnF wrappers must be deleted, not aliased or renamed.
    service_source = (ROOT / "src/cifereca/services.py").read_text(encoding="utf-8")
    assert "self.can_borrow_bnf(" not in service_source
    assert "self.borrow_bnf_manual(" not in service_source
    assert (ROOT / "src/cifereca/core/borrow_claim_observation.py").is_file()
    assert (ROOT / "tests/test_p1_4082_bnf_borrow_claim_observation.py").is_file()
