"""Explicit Numilog public detail button for a selected Core search result.

This GTK-independent adapter schedules exactly one user task per click;
the existing SearchDetailHost generation and identity guards control the
result. No network during list render, detail render, or account switch.
"""
from __future__ import annotations

from dataclasses import replace

from urllib.parse import urlsplit

from cifereca.models import SearchResult
from cifereca.task_queue import TaskPriority


def can_enrich_numilog_result(item: object) -> bool:
    """UI visibility hint only; provider will re-attest all source evidence."""
    if not isinstance(item, SearchResult):
        return False
    url = getattr(item, "friendly_url", None)
    # This is only a UI hint. Core must not encode Numilog's hostname:
    # the provider independently attests the product URL before any GET.
    # Keep a generic syntactic HTTPS gate to avoid advertising malformed links.
    if (type(url) is not str or not url or len(url) > 16384
            or any(ord(char) < 33 or ord(char) == 127 or ord(char) == 92 for char in url)):
        return False
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443)
                or not parsed.path or parsed.path == "/"
                or parsed.query or parsed.fragment):
            return False
    except ValueError:
        return False
    if item.source_provider == "numilog":
        return (item.owned is True
                and item.source_collection == "library"
                and item.source_credential not in {"", "__public__"}
                and item.download_url is None)
    return (item.source_provider == "numilog-catalog"
            and item.source_credential == "__public__"
            and item.source_collection == "public_catalog"
            and item.owned is False and item.downloadable is False
            and item.download_url is None)


def add_numilog_enrichment_action(host, selected_id, item, detail, context, *,
                                  button_factory) -> bool:
    if (not isinstance(context, tuple) or len(context) != 4
            or not can_enrich_numilog_result(item)
            or not host._search_detail_context_current(selected_id, context)):
        return False
    # Remote search rows use a provider-scoped dict key; their SearchResult.id
    # is only a display/request id. Never create a ghost lookup entry.
    def selected_row_key():
        keys = [
            key for key, current in host.search_objects.items()
            if current is item
        ]
        return keys[0] if len(keys) == 1 else None

    if selected_row_key() is None:
        return False
    button = button_factory("Enrichir depuis Numilog", "view-refresh-symbolic")

    def invoke(_button):
        if (not host._search_detail_context_current(selected_id, context)
                or selected_row_key() is None):
            return
        # Use the identity snapshot captured by the existing search detail
        # context. A changed profile invalidates both the worker's request
        # and its subsequent GTK callback.
        expected_identity_id = context[3] or ""
        def do_enrich():
            return host.service.enrich_numilog_search_result(
                item, expected_identity_id=expected_identity_id,
            )

        def success(enriched):
            if not host._search_detail_context_current(selected_id, context):
                return
            source_key = selected_row_key()
            if source_key is None:
                # Same selection id can outlive a replaced search-row object.
                # Do not publish orphaned metadata under a new mapping key.
                return
            # Build a new presentation, not an entitled/acquired file record.
            # Keep the provider's existing holdings status, not public offers.
            refreshed = replace(
                detail, title=enriched.title, author=enriched.author,
                year=enriched.published_at, thumbnail_url=enriched.thumbnail_url,
            )
            host.search_objects[source_key] = enriched
            host._apply_search_detail(selected_id, enriched, refreshed, context=context)

        def failure(_exc):
            # Never echo arbitrary private/provider exceptions, signed URLs,
            # account ids, or raw HTML to a GTK toast.
            if host._search_detail_context_current(selected_id, context):
                host.toast("Enrichissement Numilog indisponible ou non attesté.", 5)

        host.runner.submit(
            "Enrichissement public Numilog…", do_enrich,
            success, failure, control=button,
            priority=TaskPriority.USER_ACTION, kind="plugin", origin="user",
        )

    button.connect("clicked", invoke)
    host.search_page.detail.add_actions([button])
    return True
