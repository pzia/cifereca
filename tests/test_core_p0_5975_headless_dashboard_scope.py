"""#5975/#5851: dashboard Core projection is headless and navigation-independent.

The selected card is presentation state, never authority for provider GET or
the account/identity used by a synchronization worker.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.core import sync_projection
from tests.test_sync_state_contract import _service


class _Headless:
    """Delegate ordinary Core reads; fail on any GTK navigation access."""
    def __init__(self, service):
        self.service = service
        self.lookups = 0

    @property
    def current_identity(self):
        self.lookups += 1
        raise AssertionError("Core worker must not read GTK-selected identity")

    def __getattr__(self, name):
        return getattr(self.service, name)


def test_headless_dashboard_selection_is_explicit_and_immutable(tmp_path):
    service = _service(tmp_path)
    try:
        headless = _Headless(service)
        selected = sync_projection.dashboard_state(
            headless, selected_identity_id="alex",
        )
        assert [row["identity_id"] for row in selected] == [
            "default", "alex",
        ]
        assert [row["selected"] for row in selected] == [False, True]
        missing = sync_projection.dashboard_state(headless)
        assert [row["selected"] for row in missing] == [False, False]
        for bad in ("", " alex ", 0, False, NS(identity_id="alex")):
            assert not any(row["selected"] for row in sync_projection.dashboard_state(
                headless, selected_identity_id=bad,
            ))
        assert headless.lookups == 0
    finally:
        service.close()


def test_facade_captures_current_selection_only_at_synchronous_entry(tmp_path):
    service = _service(tmp_path)
    try:
        service.current_identity = NS(identity_id="alex")
        cards = service.dashboard_state()
        assert [card["selected"] for card in cards] == [False, True]
        service.current_identity = NS(identity_id="default")
        after = service.dashboard_state()
        assert [card["selected"] for card in after] == [True, False]
        # The old result does not observe later navigation changes.
        assert [card["selected"] for card in cards] == [False, True]
        service.current_identity = None
        assert not any(card["selected"] for card in service.dashboard_state())
    finally:
        service.close()


def test_dashboard_projection_has_no_current_identity_ast_attribute():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sync_projection.dashboard_state))
    assert not any(
        isinstance(n, ast.Attribute) and n.attr == "current_identity"
        for n in ast.walk(tree)
    )
