"""#6366: GTK-free N3 paint guard for Library/Recents public C fallback.

The worker's VerifiedLocalPublicCLink does not authorize painting after a
profile/account/source refresh or after a same-key UI row is replaced. This
read-only guard inspects only the *current already materialized* owner mapping.
It never reopens a local file, reads SQLite, asks a supplier, or grants rights.
"""
from __future__ import annotations

from typing import Any

from ..core.notice_c_local_origin_projection import (
    VerifiedLocalPublicCLink, valid_local_public_c_link,
)


def local_public_c_cell_current(
    host: Any, row: Any, *, service: object,
    context: tuple[object, object, object],
) -> bool:
    """Admit a delayed local public-C pixel only for the same live row."""
    active_service = getattr(host, "service", None)
    if (active_service is not service
            or type(context) is not tuple or len(context) != 3
            or (
                getattr(getattr(active_service, "current_identity", None),
                        "identity_id", None),
                getattr(active_service, "_account_context_epoch", None),
                getattr(active_service, "_source_context_epoch", None),
            ) != context
            or type(context[0]) is not str or not context[0]
            or type(context[1]) is not int
            or type(context[2]) is not int):
        return False
    link = getattr(row, "_cover_public_c_link", None)
    ref = getattr(row, "_cover_document_ref", None)
    key = getattr(row, "key", None)
    content = getattr(row, "_public_row_content", None)
    if (type(link) is not VerifiedLocalPublicCLink
            or ref != link.document
            or type(key) is not str or not key
            or type(content) is not tuple or not content
            or getattr(row, "_cover_local_evidence", None) is not None
            or getattr(row, "_cover_history_evidence", None) is not None
            or not valid_local_public_c_link(
                link, identity_id=context[0], artifact_id=link.artifact_id,
                placement_id=link.placement_id,
            )):
        return False

    view = content[0]
    if view == "library":
        if (getattr(row, "_library_ambiguous_key", False) is True
                or key in getattr(host, "_library_ambiguous_keys", ())):
            return False
        objects = getattr(host, "_library_objects", None)
    elif view == "recent":
        if getattr(row, "_recent_ambiguous_key", False) is True:
            return False
        objects = getattr(host, "_recent_objects", None)
    else:
        return False
    if type(objects) is not dict:
        return False
    mapped = objects.get(key)
    return (
        type(mapped) is dict
        and mapped.get("_public_canonical_cover_link") is link
        and mapped.get("identity_id") == context[0]
        and mapped.get("artifact_id") == link.artifact_id
        and mapped.get("placement_id") == link.placement_id
        and mapped.get("_exact_local_cover_evidence") is None
        and mapped.get("_library_view_owner") in (None, context[0])
    )


__all__ = ["local_public_c_cell_current"]
