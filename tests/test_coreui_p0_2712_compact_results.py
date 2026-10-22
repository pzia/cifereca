"""P0 #2712: the statistics appendix is optional, not an action blocker."""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.gui.maintenance_result_view import _split_compact_results


def test_incomplete_audit_limitations_stay_visible_while_counts_can_collapse():
    raw = (
        'Analyse incomplète. 600 fichier(s) observé(s), 256 lien(s) confirmé(s).'
        '\n\nAvertissements et limites :\n'
        '• Structure EPUB invalide sur certains fichiers.\n'
        '• Aucun rapprochement automatique.'
        '\n\nRésultats :\n'
        '• SHA : 600 / 600\n• Placements historiques : 273'
    )
    visible, appendix = _split_compact_results(raw)
    assert 'Analyse incomplète' in visible
    assert 'Structure EPUB invalide' in visible
    assert 'Aucun rapprochement automatique' in visible
    assert 'SHA : 600 / 600' not in visible
    assert 'SHA : 600 / 600' in appendix
    assert 'Placements historiques : 273' in appendix


def test_late_limitations_are_never_hidden_in_a_collapsed_counter_block():
    raw = (
        '600 fichiers · 256 liens'
        '\n\nRésultats :\n• Identifiants : 124'
        '\n\nAvertissements et limites :\n• Lecture seule.'
    )
    visible, appendix = _split_compact_results(raw)
    assert '600 fichiers' in visible and 'Lecture seule.' in visible
    assert 'Identifiants : 124' in appendix
    assert 'Lecture seule.' not in appendix


def test_empty_legacy_and_untrusted_payload_have_no_hidden_section():
    assert _split_compact_results('Rapport local.')[0] == 'Rapport local.'
    assert _split_compact_results('Rapport local.')[1] == ''
    assert _split_compact_results({'a': 'PRIVATE_SECRET'}) == ('', '')


def test_original_inspector_action_remains_pinned_below_report_scroller():
    path = Path(__file__).resolve().parents[1] / 'src/cifereca/gui/maintenance_result_view.py'
    source = path.read_text(encoding='utf-8')
    ast.parse(source)
    assert 'reconciliation-result-statistics' in source
    assert "details.set_expanded(False)" in source
    assert "top.add_bottom_bar(controls)" in source
    assert "body.append(controls)" not in source
    assert "self.inspector.start()" in source
    assert 'examine.connect(\'clicked\', open_inspector)' in source
    assert 'close.connect(\'clicked\', lambda *_: window.close())' in source
