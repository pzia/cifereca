"""Bound actual N3/GTK Loan texture receipts to existing N0 observer (#6168).

The N1/N2 loan bridge and LoanCoverTraceSubscriptions (#6201) own the
documentary decision, subscriptions, context invalidation and window teardown.
No duplicate cache, observer or entitlement channel is introduced here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal

from ..core.book_cover_cache import CoverCacheToken, CoverDecisionCache
from ..core.book_cover_projection import ResolvedBookCover
from ..core.book_cover_source_projection import exact_loan_cover_scope
from ..core.loan_source_identity import loan_source_key
from ..models import Loan
from .loan_cover_trace_bridge import LoanCoverTraceSubscriptions


def _context(service: Any, identity_id: str) -> tuple[object, object, object]:
    return (
        identity_id,
        getattr(service, "_account_context_epoch", None),
        getattr(service, "_source_context_epoch", None),
    )


def note_loan_cover_token(
    host: Any, token: CoverCacheToken, decision: ResolvedBookCover,
) -> None:
    """Attach the real published N2 generation to #6201's live observer."""
    if (type(token) is not CoverCacheToken
            or type(decision) is not ResolvedBookCover):
        return
    observer = getattr(host, "_loan_cover_trace_observer", None)
    service = getattr(host, "service", None)
    if type(observer) is LoanCoverTraceSubscriptions:
        observer.record_published(
            token, decision, context=_context(service, token.scope.identity_id),
        )


def loan_cover_paint_receipt(
    host: Any, loan: Loan, *, surface: Literal["list", "detail"],
    expected_url: str | None,
) -> Callable[[str, Path], None] | None:
    """One current exact Loan, one CAS generation; never a title/URL join."""
    if (type(loan) is not Loan or surface not in ("list", "detail")
            or type(expected_url) is not str or not expected_url):
        return None
    scope = exact_loan_cover_scope(loan)
    service = getattr(host, "service", None)
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    epoch = getattr(service, "_account_context_epoch", None)
    revision = getattr(host, "_loan_cover_decision_revision", None)
    observer = getattr(host, "_loan_cover_trace_observer", None)
    cache = getattr(host, "_loan_cover_decision_cache", None)
    if (scope is None or identity != loan.identity_id or type(identity) is not str
            or not callable(getattr(getattr(host, "covers", None), "probe_media", None))
            or type(epoch) is not int or epoch < 0
            or type(revision) is not int or revision < 1
            or type(observer) is not LoanCoverTraceSubscriptions
            or type(cache) is not CoverDecisionCache):
        return None
    context = _context(service, identity)
    current = observer.published_token(scope, context=context)
    if current is None:
        return None
    token, decision = current
    if (token.revision != revision or decision.status != "selected"
            or decision.uri != expected_url
            or cache.peek(scope, revision=revision) is not decision):
        return None
    key = loan_source_key(loan)

    def applied(texture_url: str, _texture_path: Path) -> None:
        if type(texture_url) is not str or texture_url != expected_url:
            return
        # The CoverView/#6199 callback is emitted only after Gdk accepted a
        # real texture, and its current widget binding generation was checked.
        # Revalidate model/epoch here because callbacks may be reentrant.
        if (getattr(host, "_closed", False)
                or getattr(getattr(service, "current_identity", None),
                           "identity_id", None) != identity
                or _context(service, identity) != context
                or getattr(host, "_loan_cover_decision_revision", None) != revision
                or getattr(host, "loan_objects", {}).get(key) is not loan
                or (surface == "detail"
                    and getattr(host, "_displayed_loan", None) is not loan)
                or cache.peek(scope, revision=revision) is not decision):
            return
        probe = getattr(getattr(host, "covers", None), "probe_media", None)
        if not callable(probe):
            return
        try:
            image = probe(expected_url)
        except (OSError, ValueError):
            return
        observed = getattr(image, "source_cached", None)
        if type(observed) is not bool:
            return
        observer.observe_texture(
            token, decision, context=context, surface=surface,
            source_cached=observed,
            preview_cached=(
                getattr(image, "preview_cached", None) is True and observed
            ),
        )

    return applied


__all__ = ["note_loan_cover_token", "loan_cover_paint_receipt"]
