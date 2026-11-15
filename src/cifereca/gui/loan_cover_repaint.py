"""In-place N4 image-only repaint for exact Loan GTK rows (#6168).

Called ONLY after a new current-identity, current-account Core Library cover
snapshot was delivered. The renderer has already projected fresh Loan rows
and Core N1/N2 chose the URLs. This adapter never opens files, fetches images,
links two books or changes an actionable Loan. If ANY non-image presentation,
ordering or source/epoch coordinate differs, fall back to MasterDetailPage's
ordinary complete row replacement.

The BookListCell's existing `thumbnail-changed` connection repaints bound
cells at the current GTK bind generation; hidden/recycled cells remain lazy.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any


_VISIBLE_NO_IMAGE = (
    "key", "title", "subtitle", "meta", "badge", "badge_style",
    "default_thumbnail_url", "cover_provider", "cover_record_id",
    "cover_title", "cover_author", "cover_year", "action_label",
    "action_icon_name",
)


def repaint_exact_loan_cover_rows(
    page: Any, new_rows: Sequence[Any], *,
    context: tuple[object, object, object],
) -> tuple[str, ...] | None:
    """Return changed SourceKeys if safely handled, otherwise None.

    All rows are validated BEFORE the first signal. An unchanged cover is a
    legitimate no-op. The caller must still call `page.show_rows(new_rows)`:
    the updated presentation signatures let its existing append-fast path
    keep GTK selection, while normal row changes use the standard splice.
    """
    if type(context) is not tuple or len(context) != 3:
        return None
    identity_id, account_epoch, source_epoch = context
    if type(identity_id) is not str or not identity_id:
        return None
    if type(new_rows) not in (list, tuple) or not new_rows:
        return None
    store = getattr(page, "store", None)
    if not callable(getattr(store, "get_n_items", None)):
        return None
    if not callable(getattr(store, "get_item", None)):
        return None
    try:
        if store.get_n_items() != len(new_rows):
            return None
        old_rows = [store.get_item(i) for i in range(len(new_rows))]
    except (AttributeError, IndexError, TypeError, ValueError):
        return None

    changed_keys: list[str] = []
    seen: set[str] = set()
    for old, new in zip(old_rows, new_rows):
        key = getattr(new, "key", None)
        if (type(key) is not str or not key or key in seen
                or getattr(old, "key", None) != key
                or getattr(old, "_loan_cover_context", None) != context
                or getattr(new, "_loan_cover_context", None) != context
                or not callable(getattr(old, "set_thumbnail_url", None))):
            return None
        seen.add(key)
        old_sig = getattr(old, "_public_row_content", None)
        new_sig = getattr(new, "_public_row_content", None)
        if (type(old_sig) is not tuple or type(new_sig) is not tuple
                or len(old_sig) != 9 or len(new_sig) != 9
                or old_sig[0] != "loan" or new_sig[0] != "loan"
                or old_sig[1] != key or new_sig[1] != key
                or old_sig[:6] != new_sig[:6]
                or old_sig[7:] != new_sig[7:]):
            return None
        if any(
            getattr(old, field, None) != getattr(new, field, None)
            for field in _VISIBLE_NO_IMAGE
        ):
            return None
        if getattr(old, "thumbnail_url", None) != getattr(new, "thumbnail_url", None):
            changed_keys.append(key)

    # These signatures include the active identity/account/source epoch.
    # The existing MasterDetailPage append-fast check will now observe the
    # same visible content and retain the original selected row/cell.
    for old, new in zip(old_rows, new_rows):
        old._public_row_content = new._public_row_content
        if old.thumbnail_url != new.thumbnail_url:
            old.set_thumbnail_url(new.thumbnail_url)
    return tuple(changed_keys)


__all__ = ["repaint_exact_loan_cover_rows"]
