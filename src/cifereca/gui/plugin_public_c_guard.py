"""#6366: exact canonical C paint lease for plugin book list/detail views.

Recommendations exposes a previously Core-attested canonical public C to the
standard public plugin adapter. A C DocumentRef is public bibliography, not an
evergreen N4 image lease for an old recommendation, profile, page or selection.
This guard only reads already-published GTK/Core state. No IO or rights.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.book_cover_projection import CoverDocumentScope
from ..core.notice_c_cover_index import is_canonical_cover_document
from ..plugin_api.models import UiBookListEntry
from .plugin_public_c_binding import public_c_binding_revision


@dataclass(frozen=True, slots=True)
class PinnedPluginPublicC:
    view: object = field(repr=False, compare=False)
    source_row: UiBookListEntry = field(repr=False, compare=False)
    service: object = field(repr=False, compare=False)
    document: CoverDocumentScope
    key: str
    source_key: str
    identity_id: str
    page_generation: int
    account_epoch: int
    source_epoch: int
    binding: object | None = field(default=None, repr=False)
    minimum_revision: int | None = None


def pin_plugin_public_c(view: Any, source: object, *, key: str) -> PinnedPluginPublicC | None:
    """Capture one original, currently published plugin C row and owner."""
    mapped = getattr(view, "_rows", None)
    window = getattr(view, "_window", None)
    service = getattr(window, "service", None)
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    page_generation = getattr(view, "_public_c_page_generation", None)
    account_epoch = getattr(service, "_account_context_epoch", None)
    source_epoch = getattr(service, "_source_context_epoch", None)
    document = getattr(source, "cover_document_ref", None)
    if (type(source) is not UiBookListEntry
            or type(mapped) is not dict
            or type(key) is not str or not key or len(key) > 1500
            or type(source.key) is not str or not source.key
            or mapped.get(key) is not source
            or type(identity) is not str or not identity
            or not is_canonical_cover_document(document)
            or type(page_generation) is not int or page_generation < 1
            or type(account_epoch) is not int or account_epoch < 0
            or type(source_epoch) is not int or source_epoch < 0
            or getattr(window, "_closed", False) is True):
        return None
    binding = source.cover_document_receipt
    minimum_revision = None
    if binding is not None:
        minimum_revision = public_c_binding_revision(window, document, binding)
        if minimum_revision is None:
            return None  # Explicit forged/revoked receipt is not a legacy ref.
    return PinnedPluginPublicC(
        view=view, source_row=source, service=service,
        document=document, key=key, source_key=source.key,
        identity_id=identity,
        page_generation=page_generation,
        account_epoch=account_epoch, source_epoch=source_epoch,
        binding=binding, minimum_revision=minimum_revision,
    )


def plugin_public_c_current(receipt: object) -> bool:
    """A new plugin render, account/source switch or reused key revokes N3."""
    if type(receipt) is not PinnedPluginPublicC:
        return False
    view = receipt.view
    window = getattr(view, "_window", None)
    service = getattr(window, "service", None)
    mapped = getattr(view, "_rows", None)
    account_epoch = getattr(service, "_account_context_epoch", None)
    source_epoch = getattr(service, "_source_context_epoch", None)
    page_generation = getattr(view, "_public_c_page_generation", None)
    return (
        getattr(window, "_closed", False) is not True
        and service is receipt.service
        and getattr(getattr(service, "current_identity", None),
                    "identity_id", None) == receipt.identity_id
        and type(account_epoch) is int and account_epoch == receipt.account_epoch
        and type(source_epoch) is int and source_epoch == receipt.source_epoch
        and type(page_generation) is int and page_generation == receipt.page_generation
        and type(mapped) is dict
        and mapped.get(receipt.key) is receipt.source_row
        # Duplicate source keys receive occurrence-local display keys;
        # their original plugin keys remain independent and never serve
        # as evidence for another entry's C.
        and receipt.source_row.key == receipt.source_key
        and receipt.source_row.cover_document_ref == receipt.document
        and receipt.source_row.cover_document_receipt is receipt.binding
        and (receipt.binding is None
             or public_c_binding_revision(window, receipt.document, receipt.binding)
                 == receipt.minimum_revision)
        and is_canonical_cover_document(receipt.document)
    )


def plugin_public_c_cell_current(item: object, receipt: object) -> bool:
    """The identical GTK list row must still carry the exact page receipt."""
    return (
        plugin_public_c_current(receipt)
        and getattr(item, "key", None) == receipt.key
        and getattr(item, "_cover_plugin_c_source", None) is receipt.source_row
        and getattr(item, "_cover_plugin_c_receipt", None) is receipt
        and getattr(item, "_cover_document_ref", None) == receipt.document
    )


def selected_plugin_public_c(view: Any, document: object) -> tuple[object, object] | None:
    """Pin detail N3 to the actual selected BookListItem and its old receipt."""
    page = getattr(view, "_page", None)
    select = getattr(getattr(page, "selection", None), "get_selected_item", None)
    selected = select() if callable(select) else None
    receipt = getattr(selected, "_cover_plugin_c_receipt", None)
    if (type(receipt) is not PinnedPluginPublicC
            or receipt.view is not view
            or document != receipt.document
            or not plugin_public_c_cell_current(selected, receipt)):
        return None
    return selected, receipt


def plugin_public_c_detail_current(
    view: Any, selected: object, receipt: object,
) -> bool:
    """The selected C is still the same plugin recommendation and image."""
    if not plugin_public_c_cell_current(selected, receipt):
        return False
    selection = getattr(getattr(view, "_page", None), "selection", None)
    get_selected = getattr(selection, "get_selected_item", None)
    return callable(get_selected) and get_selected() is selected


def staged_plugin_exact_public_c(
    view: Any, panel: Any, document: object, *,
    selection_is_current: Callable[[], bool] | None = None,
    minimum_revision: int | None = None,
):
    """Return a render-scoped N3 check for an in-place exact Search detail.

    The known-v2 exact worker verifies the source before calling the plugin
    adapter, but its canonical image may finish after the selection changes.
    Reuse the *same* original recommendation receipt as list and ordinary
    plugin detail; never trust a fresh canonical ref by itself.
    """
    from contextlib import nullcontext
    if not is_canonical_cover_document(document):
        return False, nullcontext()
    selected = selected_plugin_public_c(view, document)
    if selected is None:
        return False, nullcontext()
    row, receipt = selected

    def same_original_recommendation() -> bool:
        # The explicit-click callback comes from the recommendation that
        # launched the worker. Another recommendation may reference the SAME
        # public C edition, so the current DocumentRef / GTK row alone does
        # not prove that the old worker belongs to its replacement.
        if selection_is_current is not None:
            try:
                if (not callable(selection_is_current)
                        or selection_is_current() is not True):
                    return False
            except Exception:
                return False
        return plugin_public_c_detail_current(view, row, receipt)

    if not same_original_recommendation():
        return False, nullcontext()
    from .local_public_c_detail_guard import staged_local_public_c_detail
    return True, staged_local_public_c_detail(
        panel, same_original_recommendation,
        minimum_revision=minimum_revision,
    )


__all__ = [
    "PinnedPluginPublicC", "pin_plugin_public_c", "plugin_public_c_current",
    "plugin_public_c_cell_current", "selected_plugin_public_c",
    "plugin_public_c_detail_current", "staged_plugin_exact_public_c",
]


def request_plugin_public_web_action(view, panel, pinned, *, expected_epoch=None,
                                     selection_is_current=None):
    """Use the opaque worker-issued C binding, never a plugin's bare ref.

    Capture the rendered action-list identity as well as the original
    selection. A full detail render revokes late menu publication before
    a button even exists. No provider or rights lookup.
    """
    if type(pinned) is not tuple or len(pinned) != 2:
        return False
    selected, receipt = pinned
    if (type(receipt) is not PinnedPluginPublicC
            or receipt.binding is None
            or type(receipt.minimum_revision) is not int
            or receipt.minimum_revision < 1
            or (expected_epoch is not None and
                receipt.minimum_revision != expected_epoch)):
        return False
    window = getattr(view, "_window", None)
    page = getattr(view, "_page", None)
    actions = getattr(panel, "_action_buttons", None)
    mapped = getattr(page, "get_mapped", None)

    def current():
        if (getattr(view, "_window", None) is not window
                or getattr(view, "_page", None) is not page
                or getattr(page, "detail", None) is not panel
                or type(actions) is not list
                or getattr(panel, "_action_buttons", None) is not actions
                or not callable(mapped) or not mapped()
                or not plugin_public_c_detail_current(view, selected, receipt)):
            return False
        if selection_is_current is None:
            return True
        try:
            return callable(selection_is_current) and selection_is_current() is True
        except Exception:
            return False

    if not current():
        return False
    from .notice_c_web_action_controller import request_public_web_actions
    shown = request_public_web_actions(
        window, panel, receipt.document, expected_epoch=receipt.minimum_revision,
        current=current,
    )
    from .cross_profile_possible_lender_detail import append_c_possible_lender_summary
    append_c_possible_lender_summary(
        panel, receipt.document, expected_epoch=receipt.minimum_revision,
        current=current,
    )
    return shown
