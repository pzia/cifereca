"""#4048: obsolete loan-return feedback is not a callable GUI affordance."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "src" / "cifereca" / "gui"


def test_voluntary_return_presenter_is_absent_and_renewal_presenter_remains():
    assert not (GUI / "return_feedback.py").exists()
    assert (GUI / "loan_renewal_feedback.py").is_file()
    for name in ("loans_controller.py", "main_window.py", "detail_panel.py"):
        source = (GUI / name).read_text(encoding="utf-8")
        assert "loan_return_feedback" not in source
        assert "return_feedback import" not in source
        assert "Restituer la licence ADEPT" not in source
