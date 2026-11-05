"""Closed, UI-scoped evidence when an unavailable-action modal is presented.

#4277: a screenshot of an unexplained modal is not proof that a provider, a
titlebar entry, or an automatic task caused it. Record only positive facts.
This module never logs free-form UI messages, exception text, provider payload,
credentials, account identifiers, file paths or signed URLs.
"""
from __future__ import annotations

from contextlib import contextmanager
import re


_ACTION_ID = re.compile(r"[a-z][a-z0-9_.-]{0,79}\Z")
_ROUTE = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_TRIGGERS = frozenset({
    "user_click", "async_callback", "auto_sync", "navigation", "unknown",
})


def _registered_action_id(window: object, action: object) -> str:
    """Only a currently registered, *exact* action object has a trusted ID."""
    pages = getattr(window, "_pages", None)
    if type(pages) is not dict or len(pages) > 256:
        return "unknown"
    try:
        from .shell_model import action_registry
        for row in action_registry(pages.values()):
            if (row.action is action and type(row.key) is str
                    and _ACTION_ID.fullmatch(row.key)):
                return row.key
    except Exception:
        pass
    return "unknown"


@contextmanager
def action_dialog_context(window: object, action: object):
    """Temporary provenance for synchronous user-invoked page actions only.

    No sticky context survives a failed callback or reentrant presentation.
    In particular it cannot be reused by an automatic source sync that
    completes later, after a user clicked an unrelated header button.
    """
    sentinel = object()
    old = getattr(window, "_ui_dialog_observation_context", sentinel)
    window._ui_dialog_observation_context = {
        "trigger": "user_click",
        "action_id": _registered_action_id(window, action),
    }
    try:
        yield
    finally:
        if old is sentinel:
            try:
                delattr(window, "_ui_dialog_observation_context")
            except AttributeError:
                pass
        else:
            window._ui_dialog_observation_context = old


def _current_route(window: object) -> str:
    stack = getattr(window, "view_stack", None)
    getter = getattr(stack, "get_visible_child_name", None)
    if not callable(getter):
        return "unknown"
    try:
        route = getter()
        pages = getattr(window, "_pages", None)
        if (type(route) is str and _ROUTE.fullmatch(route)
                and type(pages) is dict and route in pages):
            return route
    except Exception:
        pass
    return "unknown"


def _scope_state(window: object) -> str:
    service = getattr(window, "service", None)
    if service is None:
        return "unknown"
    try:
        identity = getattr(service, "current_identity", None)
    except Exception:
        return "unknown"
    return "selected_identity" if identity is not None else "no_identity"


def unavailable_dialog_code(title: object, message: object) -> str | None:
    """Inspect only fixed UI phrases; never emit or stringify input content."""
    if type(title) is str and title in {"Action non disponible", "Action impossible"}:
        return "action_unavailable"
    if type(message) is str and message in {"Action non disponible", "Traitement indisponible"}:
        return "action_unavailable"
    return None


def observe_unavailable_dialog(window: object, *, title: object, message: object) -> None:
    """Emit exactly one structured observation AFTER successful presentation.

    Missing trigger, provider and task association are explicitly unknown;
    no correlation is reconstructed from timestamps, login or label text.
    Logging failure must not prevent the user from dismissing the dialog.
    """
    if getattr(window, "_closed", False):
        return
    code = unavailable_dialog_code(title, message)
    if code is None:
        return
    diagnostics = getattr(window, "diagnostics", None)
    add = getattr(diagnostics, "add", None)
    if not callable(add):
        return
    context = getattr(window, "_ui_dialog_observation_context", None)
    if type(context) is not dict:
        context = {}
    trigger = context.get("trigger")
    if type(trigger) is not str or trigger not in _TRIGGERS:
        trigger = "unknown"
    action_id = context.get("action_id")
    if type(action_id) is not str or not _ACTION_ID.fullmatch(action_id):
        action_id = "unknown"
    task_ref = context.get("task_ref")
    if type(task_ref) is not int or not 1 <= task_ref <= 2**53 - 1:
        task_ref = None
    # A source/provider cannot be inferred from the current selection, the
    # visible page, a task label or an error message. No provider is claimed.
    detail = {
        "schema_version": 1,
        "code": code,
        "trigger": trigger,
        "action_id": action_id,
        "route": _current_route(window),
        "ui_scope": _scope_state(window),
        "provider_id": "unknown",
        "task_ref": task_ref,
    }
    try:
        add("UI", "action unavailable dialog", "warning", detail=detail, level="normal")
    except Exception:
        pass  # Instrumentation never alters Gtk feedback or business effects.
