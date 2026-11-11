"""#5055: a withheld borrow action remains provider-neutral and non-mutating."""
import ast
from pathlib import Path

PANEL = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"


def test_withheld_borrow_presentation_does_not_branch_on_bnf():
    source = PANEL.read_text(encoding="utf-8")
    ast.parse(source)
    # Only the unavailable-action presentation is scoped: the ACSM action
    # elsewhere in the panel remains independently consented.
    body = source.split('row.set_title("Emprunt indisponible pour ce compte")', 1)
    assert len(body) == 2
    message, remainder = body[1].split('if item.source_provider == "paris":', 1)
    assert 'source_provider == "bnf-pret"' not in message
    assert "ne renvoie jamais automatiquement une demande" in message
    assert "pending_followup" in remainder
    assert 'cifereca-paris-borrow-followup' in remainder
