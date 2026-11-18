"""Explicit provider public detail action for a selected Core search result.

This GTK-independent adapter schedules exactly one user task per click;
the existing SearchDetailHost generation and identity guards control the
result. No network during list render, detail render, or account switch.
"""
from __future__ import annotations

from dataclasses import replace

from cifereca.core.provider_explicit_enrichment import can_enrich_provider_detail_result
from cifereca.task_queue import TaskPriority



def can_offer_provider_enrichment(service, item) -> bool:
    """No provider-name dispatch and no service/session network calls on render."""
    from inspect import getattr_static

    if not can_enrich_provider_detail_result(item):
        return False
    sessions = getattr(service, "_sessions", None)
    key_factory = getattr(service, "_session_key", None)
    if not isinstance(sessions, dict) or not callable(key_factory):
        return False
    session = sessions.get(key_factory(item.source_provider, item.source_credential))
    method = ("enrich_public_detail" if item.source_credential == "__public__"
              else "enrich_owned_public_detail")
    return callable(getattr_static(session, method, None))


def add_provider_enrichment_action(host, selected_id, item, detail, context, *,
                                  button_factory) -> bool:
    if (not isinstance(context, tuple) or len(context) != 4
            or not can_offer_provider_enrichment(host.service, item)
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
    # The action belongs to the exact Core that rendered the source row.
    # A retained GTK button cannot rebind itself to a replacement service.
    render_core = host.service
    button = button_factory("Enrichir les métadonnées", "view-refresh-symbolic")

    def invoke(_button):
        if (host.service is not render_core
                or not host._search_detail_context_current(selected_id, context)
                or selected_row_key() is None):
            return
        # Capture the exact Core callable and identity at admission. The
        # worker never consults the mutable window; Core independently
        # revalidates source/credential authorization at provider I/O.
        expected_identity_id = context[3] or ""
        admit = getattr(render_core, "admit_provider_search_enrichment", None)
        if callable(admit):
            try:
                do_enrich = admit(item, identity_id=expected_identity_id, user_confirmed=True)
            except Exception:
                host.toast("Enrichissement indisponible ou non attesté.", 5)
                return
            if not callable(do_enrich):
                return
        else:
            # Old embedded/test facades keep their synchronous read ABI.
            enrich = render_core.enrich_provider_search_result
            def do_enrich():
                return enrich(item, expected_identity_id=expected_identity_id)

        def success(enriched):
            if (host.service is not render_core
                    or not host._search_detail_context_current(selected_id, context)):
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
            if (host.service is render_core
                    and host._search_detail_context_current(selected_id, context)):
                host.toast("Enrichissement indisponible ou non attesté.", 5)

        host.runner.submit(
            "Enrichissement public…", do_enrich,
            success, failure, control=button,
            priority=TaskPriority.USER_ACTION, kind="plugin", origin="user",
        )

    button.connect("clicked", invoke)
    host.search_page.detail.add_actions([button])
    return True
