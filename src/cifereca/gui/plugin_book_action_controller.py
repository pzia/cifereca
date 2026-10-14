"""Book-detail contextual plugin actions (#1130), without GTK imports.

MainWindow owns widget creation and the public UiActionContext. The action
selection remains the Core shell_model contract, never a plugin-private API.
"""
from __future__ import annotations


def add_plugin_book_actions(window, detail, item, *, contextual_actions) -> None:
    if getattr(window, "_closed", False):
        return
    pages = getattr(window, "_pages", {}).values()
    context = window._book_action_context(item)
    buttons = []
    for _route, action, enabled in contextual_actions(pages, "book.detail", context):
        button = window.action_button(action.title, action.icon_name)
        button._cifereca_action_id = "plugin:" + action.id
        button.set_sensitive(enabled)

        def invoke(_button, *, selected_action=action, selected_context=context):
            if getattr(window, "_closed", False):
                return
            # Recheck the action's public selection against current page
            # contributions; a plugin may have been disabled or reloaded.
            current_pages = getattr(window, "_pages", {}).values()
            available = contextual_actions(current_pages, "book.detail", selected_context)
            if not any(
                candidate.id == selected_action.id and candidate is selected_action and active
                for _route, candidate, active in available
            ):
                return
            window._invoke_ui_action(
                selected_action, window._UiHost(window, selected_context),
            )

        button.connect("clicked", invoke)
        buttons.append(button)
    if buttons:
        detail.add_actions(buttons)
