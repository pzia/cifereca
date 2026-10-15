"""Identity-bound lifetime contract for source synchronization UI (#1347).

GTK-free. A queued source task or a detached button must not act on a new
identity, newly rebuilt Synchronisation page, or changed list generation.
The broker remains responsible for provider permissions and source ownership.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SyncActionScope:
    identity_id: str
    page_token: object
    preview_generation: int | None = None


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
    if not isinstance(identity_id, str) or not identity_id.strip():
        # SyncRequest(identity_id=None) can mean every identity. Never permit
        # that fallback from an identity-scoped GUI button.
        return None
    if (
        preview_generation is not None
        and getattr(window, "_sync_preview_generation", None) != preview_generation
    ):
        return None
    return SyncActionScope(
        identity_id=identity_id,
        page_token=page_token,
        preview_generation=preview_generation,
    )


def sync_action_scope_current(window, scope: SyncActionScope | None) -> bool:
    """Allow work/GTK delivery only for the exact captured UI generation."""
    if scope is None or getattr(window, "_closed", False):
        return False
    if getattr(window, "_sync_page_token", None) is not scope.page_token:
        return False
    identity = getattr(getattr(window, "service", None), "current_identity", None)
    if getattr(identity, "identity_id", None) != scope.identity_id:
        return False
    if (
        scope.preview_generation is not None
        and getattr(window, "_sync_preview_generation", None) != scope.preview_generation
    ):
        return False
    return True
