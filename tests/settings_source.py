"""Read real Settings composition and callback owners for source contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
OWNERS = (
    "settings_page", "settings_editor_actions", "settings_commit_actions",
    "settings_sync_actions", "settings_diagnostics_actions",
)


def settings_source_text():
    return "\n\n".join((ROOT / f"{name}.py").read_text(encoding="utf-8") for name in OWNERS)
