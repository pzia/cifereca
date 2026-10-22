"""#2712: long local audit synopsis cannot consume the whole workspace.

GTK/XTEST acceptance opens this same expander and tests a real wheel event.
The private detail remains inside the revocable dialog, not a public report.
"""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / 'src/cifereca/gui/library_inspection_view.py'
E2E = ROOT / 'e2e/library_inspection_acceptance.py'


def test_reconciliation_overview_collapsed_but_every_original_proof_remains():
    source = GUI.read_text(encoding='utf-8')
    ast.parse(source)
    assert "Gtk.Expander(label=(" in source
    assert "'Afficher les statistiques et le périmètre · '" in source
    assert "overview_panel.set_name('reconciliation-overview-expander')" in source
    assert 'overview_panel.set_expanded(False)' in source
    assert 'overview_panel.set_child(overview_content)' in source
    assert 'box.append(overview_panel)' in source
    for private_piece in ('origin_note', 'synopsis', 'scope_summary', 'scope', 'legend'):
        assert f'overview_content.append({private_piece})' in source
        assert f'box.append({private_piece})' not in source
    # The warning is not lost inside the optional expander.
    assert "if summary.get('status') != 'complete' or summary.get('inspection_limited'):" in source
    assert 'box.append(warning)' in source
    assert 'reconciliation-content-scroll' in source
    assert 'toolbar_view.add_bottom_bar(footer)' in source
    assert "f\"{overview.get('paths_active_missing', 0)} chemin(s) actif(s) absent(s)\"" in source


def test_physical_gtk_contract_can_reopen_summary_and_scroll_on_short_window():
    text = E2E.read_text(encoding='utf-8')
    ast.parse(text)
    assert 'P0_2712_inspection_overview_collapsed_initially' in text
    assert 'P0_2712_inspection_overview_reexpandable' in text
    assert "widgets['overview_panel'].set_expanded(True)" in text
    assert 'P0_2712_physical_wheel_scroll_reached' in text
    assert 'device.wheel(x, y, down=True, ticks=4)' in text
