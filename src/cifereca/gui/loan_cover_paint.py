"""Bind one exact Loan N2 decision to a genuine GDK texture event (#6168).

No image URLs are inferred from title, ISBN, hashes or catalogue entries.
An actionable Loan is never changed. The optional callback is installed on
one already-bound BookListItem or DetailPanel CoverView and invoked ONLY
after Gdk.Texture was decoded and Gtk.Picture accepted the pixels.

N0 rechecks SourceKey, account/source/identity epochs, N2 revision and
selected URI, then N3 examines the physical cache. A stale request returns
without an event; the actual GTK image is never hidden or re-downloaded.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..core.book_cover_cache import CoverDecisionCache
from ..core.book_cover_source_projection import exact_loan_cover_scope
from ..core.loan_source_identity import loan_source_key
from ..models import Loan
from .loan_cover_trace_bridge import LoanCoverTraceSubscriptions


def loan_cover_paint_observer(
    host: Any, loan: Loan, *, surface: str, displayed_uri: str | None,
) -> Callable[[str, Path], None] | None:
    """Build an exact post-texture callback, or no callback without N1 proof."""
    if (type(loan) is not Loan or surface not in ("list", "detail")
            or type(displayed_uri) is not str or not displayed_uri):
        return None
    service = getattr(host, "service", None)
    active = getattr(getattr(service, "current_identity", None),
                     "identity_id", None)
    if (type(active) is not str or not active or loan.identity_id != active
            or getattr(host, "_closed", False)):
        return None
    cache = getattr(host, "_loan_cover_decision_cache", None)
    observer = getattr(host, "_loan_cover_trace_observer", None)
    revision = getattr(host, "_loan_cover_decision_revision", None)
    loader = getattr(host, "covers", None)
    if (type(cache) is not CoverDecisionCache
            or type(observer) is not LoanCoverTraceSubscriptions
            or type(revision) is not int or revision < 0
            or not callable(getattr(loader, "probe_media", None))):
        return None
    scope = exact_loan_cover_scope(loan)
    if scope is None:
        return None
    context = (
        active, getattr(service, "_account_context_epoch", None),
        getattr(service, "_source_context_epoch", None),
    )
    trace = observer.snapshot(scope, context=context)
    if trace is None or trace.status != "selected":
        return None
    decision = cache.peek(scope, revision=revision)
    if (decision is None or decision.status != "selected"
            or decision.uri != displayed_uri):
        return None
    source_key = loan_source_key(loan)

    def applied(actual_uri: str, path: Path) -> None:
        # Only CoverView invokes this after a successful GTK texture set.
        # Keep a second source-context/owner check at callback delivery.
        if (actual_uri != displayed_uri or not isinstance(path, Path)
                or getattr(host, "_closed", False)
                or getattr(host, "_loan_cover_decision_revision", None) != revision
                or getattr(getattr(service, "current_identity", None),
                           "identity_id", None) != active
                or getattr(service, "_account_context_epoch", None) != context[1]
                or getattr(service, "_source_context_epoch", None) != context[2]):
            return
        if surface == "list":
            owned = getattr(host, "loan_objects", {}).get(source_key)
            if type(owned) is not Loan or loan_source_key(owned) != source_key:
                return
        else:
            displayed = getattr(host, "_displayed_loan", None)
            if (type(displayed) is not Loan
                    or loan_source_key(displayed) != source_key):
                return
        observer.record_applied_texture(
            cache, scope, context=context, revision=revision,
            uri=actual_uri, surface=surface, loader=loader,
        )

    return applied


__all__ = ["loan_cover_paint_observer"]
