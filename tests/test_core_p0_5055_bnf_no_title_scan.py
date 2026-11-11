"""#5055: BnF uses generic source convergence, no local-title preflight."""
from pathlib import Path
import ast


def test_bnf_legacy_auto_and_title_scan_engines_are_physically_absent():
    root = Path(__file__).resolve().parents[1]
    core = root / "src" / "cifereca" / "core"
    for name in (
        "bnf_auto_materialization.py",
        "local_candidate_screen.py",
        "local_physical_title_candidates.py",
    ):
        assert not (core / name).exists()

    coordination = core / "sync_coordination.py"
    tree = ast.parse(coordination.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module is not None
        and "bnf_auto_materialization" in node.module
        for node in ast.walk(tree)
    )
    assert "materialize(self, account, loan" not in coordination.read_text(
        encoding="utf-8",
    )
