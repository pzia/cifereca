"""Lifecycle-safe dispatch of public UiActionContribution callbacks (#1130).

MainWindow retains the menu and the public action host. No GTK dependency,
plugin activation or additional subscription is introduced by dispatch.
"""
from __future__ import annotations


def invoke_ui_action(window, action, host) -> None:
    """Run at most one explicit action; ignore queued clicks after teardown."""
    if getattr(window, "_closed", False):
        return
    # Dismissing a GTK popover is a synchronous signal boundary: an observer
    # may close the window or navigate to a different page before the action
    # callback is dispatched.
    route_token = getattr(window, "_route_display_token", None)
    header_token = getattr(window, "_header_action_generation", None)
    window.shell_menu.popdown()
    if (
        getattr(window, "_closed", False)
        or getattr(window, "_route_display_token", None) is not route_token
        or getattr(window, "_header_action_generation", None) is not header_token
    ):
        return
    try:
        action.callback(host)
    except Exception as exc:
        # A plugin callback may close the window before raising. In that case
        # never attempt to present a modal error on destroyed GTK widgets.
        if not getattr(window, "_closed", False):
            window.show_error(str(exc), "Action impossible")
