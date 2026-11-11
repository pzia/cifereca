"""P0 #5054: the retired Numilog preview has no Core or GTK façade.

The generic Kobo local-materialization preview remains available, but a
Numilog source must not expose a fake local-hash check or call the old worker.
"""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca"


def test_retired_numilog_preview_has_no_core_or_gtk_entrypoint():
    assert not (ROOT / "core/numilog_local_preview.py").exists()
    broker = (ROOT / "brokers.py").read_text(encoding="utf-8")
    gui = (ROOT / "gui/sync_controller.py").read_text(encoding="utf-8")
    view = (ROOT / "gui/sync_view.py").read_text(encoding="utf-8")
    for source in (broker, gui, view):
        ast.parse(source)
        assert "numilog_local_preview" not in source
    assert "Aperçu local des achats Numilog" not in gui
    assert "Vérifier les fichiers…" not in gui
    assert "def preview_local_materialization(" in broker
    assert "Aperçu local…" in gui  # Kobo-only canonical preview
