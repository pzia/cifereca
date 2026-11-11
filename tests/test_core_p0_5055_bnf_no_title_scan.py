"""#5055: supplier delivery never depends on inferred local title/OPF matches."""
from pathlib import Path
import ast


def test_bnf_runtime_cannot_use_legacy_title_or_opf_scanners():
    root = Path(__file__).resolve().parents[1]
    core = root / "src" / "cifereca" / "core"
    assert not (core / "local_candidate_screen.py").exists()
    assert not (core / "local_physical_title_candidates.py").exists()

    code = (core / "bnf_auto_materialization.py").read_text(encoding="utf-8")
    tree = ast.parse(code)
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module is not None
        and any(name in node.module for name in (
            "local_candidate_screen", "local_physical_title_candidates",
        ))
        for node in ast.walk(tree)
    )
    assert "screen_local_candidates" not in code
    assert "observe_physical_local_titles" not in code
    # A title match is never an exact supplier-origin artifact or consent.
    assert "local_receipt_file_verified" in code
    assert "select_exact_loan_offer" in code
