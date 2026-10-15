from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path
from types import SimpleNamespace

from cifereca.gui.booking_view import booking_presentation, booking_rank_label


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src" / "cifereca" / "gui" / "main_window.py"


def _method(class_name: str, method_name: str) -> str:
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def _booking(**values):
    defaults = dict(state="active", rank=None, availability_date=None, available_until=None)
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_waiting_booking_uses_french_ordinal_queue_label():
    assert booking_rank_label("1") == "1er en file"
    assert booking_rank_label("10") == "10e en file"
    assert booking_presentation(_booking(rank="10")).status_label == "En attente · 10e en file"


def test_available_booking_prioritizes_actionable_deadline():
    presentation = booking_presentation(_booking(
        state="active",
        rank="2",
        availability_date="2026-10-08T00:00:00+00:00",
        available_until="2026-10-15T23:59:00+00:00",
    ))
    assert presentation.status_label == "Disponible jusqu’au 15/10/2026"


def test_ready_cancelled_expired_and_unknown_states_never_expose_raw_codes():
    assert booking_presentation(_booking(state="ready", availability_date="2026-10-08")).status_label == "Prête à retirer"
    assert booking_presentation(_booking(state="cancelled")).status_label == "Annulée"
    assert booking_presentation(_booking(state="expired")).status_label == "Expirée"
    assert booking_presentation(_booking(state="provider_internal_code")).status_label == "État à vérifier"


def test_booking_list_and_detail_share_same_business_projection():
    owner = ast.parse(MAIN.with_name("secondary_results_controller.py").read_text(encoding="utf-8"))
    listing = ast.unparse(next(node for node in owner.body
                               if isinstance(node, ast.FunctionDef)
                               and node.name == "populate_bookings"))
    assert "secondary_results_controller.populate_bookings" in _method("MainWindow", "_populate_bookings")
    detail = _method("DetailPanel", "show_booking")
    assert "presentation = booking_presentation(item)" in listing
    assert "presentation.status_label" in listing
    assert "presentation = booking_presentation(item)" in detail
    assert "('État', presentation.status_label)" in detail
    assert "item.state" not in detail
    assert "'Rang'" not in detail
