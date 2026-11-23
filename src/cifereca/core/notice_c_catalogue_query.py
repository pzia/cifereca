"""#2385: a single public C catalogue query drives all six presentations."""
from __future__ import annotations

from .notice_c_catalogue_views import project_public_catalogue_view
from .notice_c_public_search import search_public_notice_c


def search_public_catalogue_view(
    storage, query: str, *, mode: str, field: str = "all",
    scope: str = "all", provider_ids: tuple[str, ...] = (),
    page: int = 0, page_size: int = 50, max_scan: int = 10000,
    now: int | None = None, content_types: tuple[str, ...] = (),
    age: int | None = None, age_kind: str | None = None,
):
    """Run the existing authoritative C cursor, then project a requested view.

    This read-only worker API does not read any per-profile library/loan state.
    Sources mode groups by canonical C to preserve source contributions.
    """
    if type(mode) is not str or mode not in (
        "list", "grid", "compact", "detail", "sources", "timeline",
    ):
        raise ValueError("invalid_public_catalogue_view")
    result = search_public_notice_c(
        storage, query, field=field, scope=scope,
        provider_ids=provider_ids, page=page, page_size=page_size,
        max_scan=max_scan, now=now, content_types=content_types,
        age=age, age_kind=age_kind, group_by_document=True,
    )
    return project_public_catalogue_view(result, mode=mode)
