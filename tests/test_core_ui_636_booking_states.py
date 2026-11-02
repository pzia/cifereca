"""CoreUI P1 #4147: no booking UI survives; old stored model is still parseable."""
from __future__ import annotations

import ast
from dataclasses import asdict
from pathlib import Path

from cifereca.models import Booking

ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def test_retired_booking_detail_renderer_and_ui_module_are_absent():
    detail = ast.parse((ROOT / "detail_panel.py").read_text(encoding="utf-8"))
    main = ast.parse((ROOT / "main_window.py").read_text(encoding="utf-8"))
    detail_panel = next(node for node in detail.body
                        if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    main_window = next(node for node in main.body
                       if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    assert "show_booking" not in {
        node.name for node in detail_panel.body if isinstance(node, ast.FunctionDef)
    }
    assert not {"refresh_bookings", "_build_bookings_page",
                "_populate_bookings", "_booking_selected"} & {
        node.name for node in main_window.body if isinstance(node, ast.FunctionDef)
    }
    assert not (ROOT / "booking_view.py").exists()
    assert "booking_presentation" not in (ROOT / "detail_panel.py").read_text(encoding="utf-8")
    assert "Booking" not in (ROOT / "main_window.py").read_text(encoding="utf-8")


def test_historical_booking_model_remains_decodable_without_ui_entrypoint():
    # Preserve the v1 data model for old snapshots/rollback but do not emit
    # a rendered action, a provider GET or an availability claim.
    old = {
        "id": "legacy-synthetic", "provider": "paris", "medium": "digital",
        "title": "Document historique", "state": "ready",
        "raw": {"ignored_provider_payload": "synthetic"},
    }
    booking = Booking(**old)
    assert asdict(booking)["id"] == "legacy-synthetic"
    assert booking.state == "ready"
    assert booking.source_provider == "paris"
