"""GTK-free loan list/detail bridge to the shared N1/N2 cover decision (#6168).

Use ONLY the same fully materialised, current-account Library snapshot that
the Loans GTK controller already admitted. A loan refresh and a Library
refresh each advance one window-local producer revision; the old decision
cannot outlive that revision. The Core N1 producer validates every exact
borrower, Entitlement, Artifact and Placement proof before N2 publication.

No network, GTK, EPUB scan, title/ISBN matching or loan DTO mutation here.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..models import Loan
from ..core.book_cover_cache import CoverDecisionCache
from ..core.book_cover_projection import ResolvedBookCover
from ..core.book_cover_source_projection import (
    exact_loan_cover_scope, project_exact_loan_cover,
)


def loan_cover_decision(
    host: Any, loan: Loan, library_rows: tuple[Mapping[str, object], ...],
) -> ResolvedBookCover | None:
    """One Core decision per exact Loan+producer revision, shared by two views."""
    if type(loan) is not Loan:
        return None
    active = getattr(
        getattr(getattr(host, "service", None), "current_identity", None),
        "identity_id", None,
    )
    epoch = getattr(getattr(host, "service", None), "_account_context_epoch", None)
    projection = getattr(host, "_loan_cover_projection", None)
    if (type(active) is not str or loan.identity_id != active
            or type(projection) is not tuple or len(projection) != 3
            or projection[0] != epoch or projection[1] != active
            or projection[2] is not library_rows):
        return None
    revision = getattr(host, "_loan_cover_decision_revision", 0)
    if (type(revision) is not int or revision < 1
            or type(library_rows) is not tuple or not library_rows):
        # A failed Library read and an authoritative empty snapshot look the
        # same in the legacy worker; do not memoize a negative proof here.
        return None
    scope = exact_loan_cover_scope(loan)
    if scope is None:
        return None
    cache = getattr(host, "_loan_cover_decision_cache", None)
    if cache is None:
        cache = CoverDecisionCache(max_scopes=1024)
        host._loan_cover_decision_cache = cache
    if type(cache) is not CoverDecisionCache:
        return None

    # N0 subscribes to actual N2 refresh/resolved/invalidation events.
    # A new identity/account/source epoch resets the observation rather than
    # replaying a previous presentation as if it had been repainted.
    from .loan_cover_trace_bridge import LoanCoverTraceSubscriptions
    observer = getattr(host, "_loan_cover_trace_observer", None)
    if observer is None:
        observer = LoanCoverTraceSubscriptions(max_scopes=128)
        host._loan_cover_trace_observer = observer
    if type(observer) is LoanCoverTraceSubscriptions:
        observer.watch(
            cache, scope,
            context=(active, epoch, getattr(
                getattr(host, "service", None), "_source_context_epoch", None,
            )),
        )

    hit = cache.peek(scope, revision=revision)
    if hit is not None:
        return hit
    decision = project_exact_loan_cover(
        loan, library_rows, revision=revision, snapshot_complete=True,
    )
    if decision is None:
        return None
    try:
        token = cache.begin(scope, revision=revision)
    except ValueError:
        # An older worker must not invalidate a newer decision.
        return None
    if cache.publish_resolved(token, decision):
        # Explain a missing loan cover once per published N2 revision, only
        # when detailed diagnostics are requested. No second N1 resolution,
        # supplier access, inferred association or raw coordinates.
        if decision.status == "missing":
            _report_missing_cover(host, loan, library_rows, decision)
        return decision
    return cache.peek(scope, revision=revision)


def _report_missing_cover(host, loan, rows, decision):
    from ..diagnostics import Diagnostics
    from ..core.book_cover_diagnostics import describe_loan_cover_gap

    diagnostics = getattr(host, "diagnostics", None)
    if (type(diagnostics) is not Diagnostics or not diagnostics.enabled
            or diagnostics.level not in ("detailed", "verbose", "trace")):
        return
    try:
        gap = describe_loan_cover_gap(loan, rows, decision)
        diagnostics.add(
            "COVER", "loan cover gap", "ok", detail=gap.public_fields(),
            level="detailed",
        )
    except Exception:
        # Observation is not part of admission or presentation; a diagnostic
        # sink failure must not remove a valid Core decision.
        pass


def current_loan_embedded_conflict(host: Any, loan: Loan) -> bool:
    """An admitted current N2 conflict forbids every weaker cover route.

    Do not infer a conflict from a missing/legacy snapshot or mint a new
    association. The existing scoped N2 producer owns the decision.
    """
    projection = getattr(host, "_loan_cover_projection", None)
    if type(projection) is not tuple or len(projection) != 3:
        return False
    rows = projection[2]
    if type(rows) is not tuple or not rows:
        return False
    decision = loan_cover_decision(host, loan, rows)
    return (decision is not None and decision.status == "ambiguous"
            and decision.tier == "embedded")


__all__ = ["loan_cover_decision", "current_loan_embedded_conflict"]
