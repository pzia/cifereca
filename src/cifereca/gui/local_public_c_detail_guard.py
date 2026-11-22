"""P0 #6366: one-current-row guard for local Library/Recent public C detail.

A completed OriginLink is documentary evidence only for its original
Artifact/Placement and local owner; a N3 callback must not paint under a
new owner, service, generation or same-key source replacement.

This module is GTK-free and never reads SQLite, the filesystem or a provider.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

from ..core.notice_c_local_origin_projection import (
    VerifiedLocalPublicCLink, valid_local_public_c_link,
)


def local_public_c_detail_current(
    window: Any, row: Any, item: object, link: object, *,
    view: str, service: object, identity_id: object,
    account_epoch: object, source_epoch: object,
) -> bool:
    """Check a captured exact list selection after any async N3 response."""
    active_service = getattr(window, "service", None)
    if (active_service is not service
            or type(identity_id) is not str or not identity_id
            or type(account_epoch) is not int
            or type(source_epoch) is not int
            or getattr(getattr(active_service, "current_identity", None),
                       "identity_id", None) != identity_id
            or getattr(active_service, "_account_context_epoch", None) != account_epoch
            or getattr(active_service, "_source_context_epoch", None) != source_epoch
            or type(item) is not dict
            or type(link) is not VerifiedLocalPublicCLink
            or item.get("_public_canonical_cover_link") is not link
            or item.get("identity_id") != identity_id
            or item.get("artifact_id") != link.artifact_id
            or item.get("placement_id") != link.placement_id
            or item.get("_exact_local_cover_evidence") is not None
            or item.get("_library_view_owner") not in (None, identity_id)
            or not valid_local_public_c_link(
                link, identity_id=identity_id,
                artifact_id=item.get("artifact_id"),
                placement_id=item.get("placement_id"),
            )):
        return False
    key = getattr(row, "key", None)
    content = getattr(row, "_public_row_content", None)
    if (type(key) is not str or not key
            or type(content) is not tuple or not content
            or content[0] != view
            or getattr(row, "_cover_public_c_link", None) is not link
            or getattr(row, "_cover_document_ref", None) != link.document):
        return False
    if view == "library":
        if (getattr(row, "_library_ambiguous_key", False) is True
                or key in getattr(window, "_library_ambiguous_keys", ())):
            return False
        objects = getattr(window, "_library_objects", None)
    elif view == "recent":
        if (getattr(row, "_recent_ambiguous_key", False) is True
                or key in getattr(window, "_recent_ambiguous_keys", ())):
            return False
        objects = getattr(window, "_recent_objects", None)
    else:
        return False
    return type(objects) is dict and objects.get(key) is item


def local_public_c_detail_check(
    window: Any, row: Any, item: object, link: object, *, view: str,
) -> Callable[[], bool]:
    """Capture, not rediscover, the owner/source epochs at selection."""
    service = getattr(window, "service", None)
    identity_id = getattr(
        getattr(service, "current_identity", None), "identity_id", None,
    )
    account_epoch = getattr(service, "_account_context_epoch", None)
    source_epoch = getattr(service, "_source_context_epoch", None)
    return lambda: local_public_c_detail_current(
        window, row, item, link, view=view, service=service,
        identity_id=identity_id, account_epoch=account_epoch,
        source_epoch=source_epoch,
    )


@contextmanager
def staged_local_public_c_detail(
    detail: Any, is_current: Callable[[], bool], *,
    minimum_revision: int | None = None,
) -> Iterator[None]:
    """Use one callback for the synchronous detail header, not future rows."""
    if (not callable(is_current)
            or getattr(detail, "_next_local_public_c_check", None) is not None):
        raise ValueError("invalid_local_public_c_detail_stage")
    if minimum_revision is not None and (
        type(minimum_revision) is not int or not 1 <= minimum_revision < 2**63
    ):
        raise ValueError("invalid_public_c_detail_epoch")
    detail._next_local_public_c_check = is_current
    detail._next_public_c_minimum_revision = minimum_revision
    try:
        yield
    finally:
        detail._next_local_public_c_check = None
        detail._next_public_c_minimum_revision = None


__all__ = [
    "local_public_c_detail_current", "local_public_c_detail_check",
    "staged_local_public_c_detail",
]


def begin_local_public_web_selection(window, view):
    """Revoke outstanding actions on every selection, including same-row ABA."""
    token = object()
    setattr(window, "_public_web_selection_" + view, token)
    return token


def local_public_web_selection_check(window, row, *, view, token, source_current):
    """An action requires the exact selected row and visible view as well as C."""
    def current():
        if (getattr(window, "_closed", False)
                or getattr(window, "_public_web_selection_" + view, None) is not token
                or not source_current()):
            return False
        stack = getattr(window, "view_stack", None)
        page = getattr(window, view + "_page", None)
        selection = getattr(page, "selection", None)
        selected = getattr(selection, "get_selected_item", None)
        visible = getattr(stack, "get_visible_child_name", None)
        return (callable(visible) and visible() == view
                and callable(selected) and selected() is row)
    return current


def request_local_public_web_action(window, detail, row, link, *, view,
                                    token, source_current):
    """Reuse the standard public C worker without a provider lookup."""
    current = local_public_web_selection_check(
        window, row, view=view, token=token, source_current=source_current,
    )
    from ..storage import Storage
    if (not current()
            or not isinstance(getattr(window.service, "storage", None), Storage)):
        return False
    from gi.repository import Gtk
    from .notice_c_web_action_controller import request_public_web_actions
    return request_public_web_actions(
        window, detail, link.document, expected_epoch=link.epoch,
        current=current, Gtk=Gtk,
    )
