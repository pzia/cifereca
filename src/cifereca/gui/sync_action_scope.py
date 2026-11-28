"""Identity-bound lifetime contract for source synchronization UI (#1347).

GTK-free. A queued source task or a detached button must not act on a new
identity, newly rebuilt Synchronisation page, or changed list generation.
The broker remains responsible for provider permissions and source ownership.
"""
from __future__ import annotations

from dataclasses import dataclass

from cifereca.plugin_api.page_lifecycle import PageViewContext


@dataclass(frozen=True, slots=True)
class SyncActionScope:
    identity_id: str
    page_token: object
    preview_generation: int | None = None
    identity_generation: int | None = None
    view_context: PageViewContext | None = None


def capture_sync_action_scope(
    window, *, page_token: object, preview_generation: int | None = None,
) -> SyncActionScope | None:
    """Capture only a currently displayed, explicit identity's source scope."""
    if getattr(window, "_closed", False):
        return None
    if getattr(window, "_sync_page_token", None) is not page_token:
        return None
    identity = getattr(getattr(window, "service", None), "current_identity", None)
    identity_id = getattr(identity, "identity_id", None)
    if type(identity_id) is not str or not identity_id.strip():
        # SyncRequest(identity_id=None) can mean every identity. Never permit
        # that fallback from an identity-scoped GUI button.
        return None
    # GTK generations are Core-owned exact integers. Plugin-owned subclasses
    # can override equality and must never participate in an action lease.
    if preview_generation is not None and (
        type(preview_generation) is not int
        or type(getattr(window, "_sync_preview_generation", None)) is not int
        or window._sync_preview_generation != preview_generation
    ):
        return None
    identity_generation = getattr(window, "_identity_context_generation", None)
    if identity_generation is not None and type(identity_generation) is not int:
        return None
    dispatcher = getattr(window, "_page_lifecycle_dispatcher", None)
    view_context = None
    if dispatcher is not None and dispatcher.handles("sync"):
        view_context = dispatcher.current_view("sync")
        if (view_context is None or view_context.identity_id != identity_id
                or not view_context.is_current()):
            return None
    return SyncActionScope(
        identity_id=identity_id,
        page_token=page_token,
        preview_generation=preview_generation,
        identity_generation=identity_generation,
        view_context=view_context,
    )


def sync_action_scope_current(window, scope: SyncActionScope | None) -> bool:
    """Allow work/GTK delivery only for the exact captured UI generation."""
    if type(scope) is not SyncActionScope or getattr(window, "_closed", False):
        return False
    if type(scope.identity_id) is not str or not scope.identity_id.strip():
        return False
    dispatcher = getattr(window, "_page_lifecycle_dispatcher", None)
    if dispatcher is not None and dispatcher.handles("sync"):
        view = scope.view_context
        current = dispatcher.current_view("sync")
        if (type(view) is not PageViewContext or current is None
                or view.generation != current.generation
                or not view.is_current()):
            return False
    elif scope.view_context is not None:
        # A previously opted-in scope must not fall back to legacy authority.
        return False
    if getattr(window, "_sync_page_token", None) is not scope.page_token:
        return False
    identity = getattr(getattr(window, "service", None), "current_identity", None)
    identity_id = getattr(identity, "identity_id", None)
    if type(identity_id) is not str or identity_id != scope.identity_id:
        return False
    # A -> B -> A may reuse the visible identity ID, but not the owner's
    # generation; never deliver an old source operation into the new session.
    if scope.identity_generation is not None and (
        type(scope.identity_generation) is not int
        or type(getattr(window, "_identity_context_generation", None)) is not int
        or window._identity_context_generation != scope.identity_generation
    ):
        return False
    if scope.preview_generation is not None and (
        type(scope.preview_generation) is not int
        or type(getattr(window, "_sync_preview_generation", None)) is not int
        or window._sync_preview_generation != scope.preview_generation
    ):
        return False
    return True


def schedule_contention_expiry(window, scope, rows, schedule):
    """One read-only refresh, restricted to the captured visible generation."""
    if not any(getattr(row, "contention_observed", False) is True for row in rows):
        return
    def expire():
        if sync_action_scope_current(window, scope):
            window._refresh_sync_page()
        return False
    schedule(30, expire)
