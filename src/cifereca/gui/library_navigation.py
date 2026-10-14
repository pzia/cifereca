"""Headless Library pending-selection orchestration (#1129).

MainWindow owns identity, filtered GTK pages, and navigation.  Pending book
identity/generation state remains window-scoped; no filter is reset here.
"""
from __future__ import annotations


def open_library_item(window, key: str) -> None:
    """Navigate to Library while retaining the book *and identity* context."""
    if getattr(window, "_closed", False):
        return
    current = getattr(getattr(window, "service", None), "current_identity", None)
    window._pending_library_selection_key = str(key)
    window._pending_library_selection_identity_id = str(
        getattr(current, "identity_id", "") or ""
    )
    window._pending_library_selection_generation = getattr(
        window, "_identity_context_generation", None
    )
    window.navigate("library")
    window._select_pending_library_item()


def select_pending_library_item(window) -> bool:
    if getattr(window, "_closed", False):
        return False
    key = getattr(window, "_pending_library_selection_key", None)
    if not key:
        return False
    pending_identity = getattr(window, "_pending_library_selection_identity_id", None)
    pending_generation = getattr(window, "_pending_library_selection_generation", None)
    current = getattr(getattr(window, "service", None), "current_identity", None)
    current_identity = str(getattr(current, "identity_id", "") or "")
    generation = getattr(window, "_identity_context_generation", None)

    def clear() -> None:
        window._pending_library_selection_key = None
        window._pending_library_selection_identity_id = None
        window._pending_library_selection_generation = None

    # A key (including a shared content SHA) is not permission to carry a
    # pending selection across identities, nor across an A -> B -> A cycle.
    if (pending_identity and current_identity != pending_identity) or (
        pending_generation is not None and generation != pending_generation
    ):
        clear()
        return False
    if not hasattr(window, "library_page"):
        return False
    if window.library_page.select_key(key):
        clear()
        return True
    entries = list(getattr(window, "_library_entries_cache", ()))
    if not entries:
        # Keep the request while the initial Library refresh is pending.
        return False
    full_keys = {str(item.get("key") or "") for item in entries}
    if key in full_keys:
        window.toast("Ce livre est masqué par les filtres actifs de la Bibliothèque.", 5)
    else:
        window.toast("Ce livre n’est plus présent dans la Bibliothèque.", 5)
    clear()
    return False
