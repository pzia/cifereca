"""P0 #1130 — navigation history never resurrects removed identity filters."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.navigation_history import NavigationState
from cifereca.gui.navigation_controller import restore_navigation_state



def _restore_callback():
    # Execute the real owner, not a duplicate AST body from MainWindow.
    return restore_navigation_state


def _host(*, current="bob", known=("bob",), route="library"):
    calls = []
    identities = {key: NS(identity_id=key) for key in known}
    service = NS(
        current_identity=NS(identity_id=current) if current else None,
        identity_by_id=lambda ident: identities.get(ident),
    )
    controls = NS(set_state=lambda value: calls.append(("filters", value)))
    nav_row = object()
    host = NS(
        service=service, _pages={route: object()},
        _restoring_navigation=False,
        library_controls=controls,
        nav_rows={route: nav_row},
        nav_list=NS(select_row=lambda row: calls.append(("nav_row", row))),
        _show_route=lambda name: calls.append(("route", name)),
        _render_library=lambda: calls.append(("render", None)),
        _update_navigation_buttons=lambda: calls.append(("buttons", None)),
    )
    def activate(identity_id):
        calls.append(("identity", identity_id))
        service.current_identity = identities[identity_id]
    host._activate_identity = activate
    return host, calls


def test_removed_identity_cannot_restore_old_search_filters_on_another_user():
    host, calls = _host(current="bob", known=("bob",))
    restore = _restore_callback()
    restore(host, NavigationState("library", "alice", "PRIVATE_ALICE_FILTER"))
    assert calls == [("buttons", None)]
    assert host.service.current_identity.identity_id == "bob"
    assert host._restoring_navigation is False


def test_same_identity_pointer_is_not_proof_that_identity_still_exists():
    host, calls = _host(current="alice", known=("bob",))
    _restore_callback()(
        host, NavigationState("library", "alice", "STALE_ALICE_FILTER"),
    )
    assert calls == [("buttons", None)]
    assert host._restoring_navigation is False


def test_existing_identity_restores_before_its_filters_and_page():
    host, calls = _host(current="bob", known=("bob", "alice"))
    _restore_callback()(host, NavigationState("library", "alice", "alice-query"))
    assert calls[0] == ("identity", "alice")
    assert calls[1] == ("filters", "alice-query")
    assert [name for name, _value in calls[2:]] == [
        "nav_row", "route", "render", "buttons",
    ]
    assert host.service.current_identity.identity_id == "alice"
    assert host._restoring_navigation is False


def test_history_without_identity_keeps_current_context():
    host, calls = _host(current="bob", known=("bob",))
    _restore_callback()(host, NavigationState("library", None, "shared-query"))
    assert calls[0] == ("filters", "shared-query")
    assert not any(name == "identity" for name, _value in calls)
    assert host.service.current_identity.identity_id == "bob"
