"""GTK smoke uploads only bounded verdicts, never user-visible pixels/logs (#1143)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/gtk-smoke.yml"


def test_gtk_smoke_publishes_no_raw_captures_or_xdg_data():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert 'name: gtk-smoke-verdict' in source
    assert 'retention-days: 2' in source
    assert 'gtk-smoke-public/result.json' in source
    assert 'name: gtk-smoke-evidence' not in source
    assert 'path: |' not in source
    assert 'path: environnements/chatgpt-work/evidence/' not in source
    assert '"capture_generated": (artifacts / "core-smoke.png").is_file()' in source
    assert '"report_generated": (artifacts / "core-smoke.json").is_file()' in source
    assert 'evidence/xvfb.log' not in source
    assert 'evidence/dbus.log' not in source
