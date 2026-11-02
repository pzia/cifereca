"""#4147: Paris simulator must not expose retired reservation HTTP operations."""
from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "paris" / "pseudo_paris_service.py"


def test_paris_simulator_reservation_operations_are_absent():
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "list_bookings" not in methods
    # Cache's legacy data-cleaning method may remain, but no HTTP handler
    # can cancel a reservation.
    assert "ListBookings" not in text
    assert "CancelBookings" not in text

    # Neither the inventory nor the valid loan operations are removed.
    for retained in ("ListLoans", "ListHandings", "DownloadDigitalDocument", "RenewLoans"):
        assert retained in text


def test_no_dispatch_reservation_mutation_or_lookup():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert "list_bookings" not in calls
    # Legacy cache cancellation is deliberately left for isolated stored
    # simulator fixtures, never called from a live request handler.
    assert "cancel_booking" not in calls
