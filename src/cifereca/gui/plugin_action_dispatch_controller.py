"""Lifecycle-safe dispatch of public UiActionContribution callbacks (#1130).

MainWindow retains the menu and the public action host. No GTK dependency,
plugin activation or additional subscription is introduced by dispatch.
"""
from __future__ import annotations

from ..plugin_api import UserFacingError


def invoke_ui_action(window, action, host) -> None:
    """Run at most one explicit action; ignore queued clicks after teardown."""
    if getattr(window, "_closed", False):
        return
    # Dismissing a GTK popover is a synchronous signal boundary: an observer
    # may close the window or navigate to a different page before the action
    # callback is dispatched.
    route_token = getattr(window, "_route_display_token", None)
    header_token = getattr(window, "_header_action_generation", None)
    identity_epoch = getattr(window, "_identity_context_generation", None)

    def context_current() -> bool:
        return (
            not getattr(window, "_closed", False)
            and getattr(window, "_route_display_token", None) is route_token
            and getattr(window, "_header_action_generation", None) is header_token
            and getattr(window, "_identity_context_generation", None) == identity_epoch
        )

    window.shell_menu.popdown()
    if not context_current():
        return
    try:
        action.callback(host)
    except Exception as exc:
        # A plugin callback may close the window before raising. In that case
        # never attempt to present a modal error on destroyed GTK widgets.
        # The action may have changed page, header or identity *during*
        # its callback, then raised. A late error belongs to the old UI
        # context and must not open a modal over a newly connected source.
        if context_current():
            # Arbitrary plugin exceptions can contain signed URLs, secrets,
            # private paths and HTTP payloads. Only the public exception
            # contract explicitly marks its user_message as UI-safe.
            message = exc.user_message if isinstance(exc, UserFacingError) else "Traitement indisponible"
            # The public action object is resolved against the current live
            # registry; never derive a name from untrusted exception text.
            # The context exists only during this synchronous dialog call.
            from .ui_dialog_observation import action_dialog_context
            with action_dialog_context(window, action):
                window.show_error(message, "Action impossible")
