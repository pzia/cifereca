"""Public book-reference recommendation target resolution (#1129).

This is a CoreUI discovery adapter, not a plugin implementation. Only a
declared recommendation capability plus the public structural protocol may
produce an actionable target. No plugin service is activated or instantiated.
"""
from __future__ import annotations


def book_recommendation_target(window, item, *, BookReferenceRecommendationService):
    if getattr(window, "_closed", False):
        return None
    manager = getattr(window, "plugin_manager", None)
    if manager is None:
        return None
    context = window._book_action_context(item)
    book = context.book
    if book is None:
        return None
    try:
        services = manager.services("recommendation")
    except Exception:
        return None
    for resolved in services:
        if "recommendation.book-reference" not in set(resolved.capabilities):
            continue
        service = resolved.service
        if isinstance(service, BookReferenceRecommendationService):
            return service, book
    return None
