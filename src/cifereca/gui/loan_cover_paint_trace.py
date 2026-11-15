"""N0 physical/GTK receipts for exact borrower-scoped Loan covers (#6168).

The N1/N2 loan adapter owns documentary admission; this module only observes
its already-selected, immutable decision. Never attach a Library cover or
provider thumbnail to a different borrower/Loan by title, ISBN, URL or SHA.
The optional GTK receipt is called AFTER an actual Gdk.Texture is applied.
It uses N3's read-only byte-cache probe and emits no network/EPUB operations.

All subscriptions/observations belong to the live MainWindow and are bounded.
A recycled cell, stale Loan DTO, account A→B→A or older provider revision
cannot claim a later source's list/detail paint.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Callable, Literal

from ..core.book_cover_cache import CoverCacheToken, CoverDecisionCache
from ..core.book_cover_projection import (
    CoverDocumentScope, ResolvedBookCover,
)
from ..core.book_cover_trace import CoverLifecycleTrace
from ..core.book_cover_source_projection import exact_loan_cover_scope
from ..core.loan_source_identity import loan_source_key
from ..models import Loan

_MAX_TRACKED_LOANS = 512


def subscribe_loan_cover_trace(
    host: Any, cache: CoverDecisionCache, scope: CoverDocumentScope,
) -> None:
    """Register BEFORE N2 begin/publish so no generated event is missed."""
    covers = getattr(host, "covers", None)
    if (type(cache) is not CoverDecisionCache
            or type(scope) is not CoverDocumentScope
            or not callable(getattr(covers, "probe_media", None))):
        return
    trace = getattr(host, "_loan_cover_lifecycle", None)
    if trace is None:
        trace = CoverLifecycleTrace(max_scopes=_MAX_TRACKED_LOANS)
        host._loan_cover_lifecycle = trace
    if type(trace) is not CoverLifecycleTrace:
        return
    handles = getattr(host, "_loan_cover_trace_subscriptions", None)
    if handles is None:
        handles = OrderedDict()
        host._loan_cover_trace_subscriptions = handles
    if type(handles) is not OrderedDict:
        return
    if scope in handles:
        handles.move_to_end(scope)
        return
    handle = trace.attach(cache, scope)
    handles[scope] = handle
    while len(handles) > _MAX_TRACKED_LOANS:
        evicted_scope, old = handles.popitem(last=False)
        old.cancel()
        tokens = getattr(host, "_loan_cover_trace_tokens", None)
        if type(tokens) is OrderedDict:
            tokens.pop(evicted_scope, None)


def note_loan_cover_token(
    host: Any, token: CoverCacheToken, decision: ResolvedBookCover,
) -> None:
    """Keep the actual N2 CAS token only for an observed qualified source."""
    if (type(token) is not CoverCacheToken
            or type(decision) is not ResolvedBookCover
            or token.scope != decision.scope
            or token.revision != decision.revision):
        return
    subscriptions = getattr(host, "_loan_cover_trace_subscriptions", None)
    if (type(subscriptions) is not OrderedDict
            or token.scope not in subscriptions):
        return
    tokens = getattr(host, "_loan_cover_trace_tokens", None)
    if tokens is None:
        tokens = OrderedDict()
        host._loan_cover_trace_tokens = tokens
    if type(tokens) is not OrderedDict:
        return
    tokens[token.scope] = (token, decision)
    tokens.move_to_end(token.scope)
    while len(tokens) > _MAX_TRACKED_LOANS:
        tokens.popitem(last=False)


def loan_cover_paint_receipt(
    host: Any, loan: Loan, *, surface: Literal["list", "detail"],
    expected_url: str | None,
) -> Callable[[], None] | None:
    """Return a no-arg GTK callback only for a live, selected N2 decision."""
    if type(loan) is not Loan or surface not in ("list", "detail"):
        return None
    scope = exact_loan_cover_scope(loan)
    if scope is None or type(expected_url) is not str or not expected_url:
        return None
    service = getattr(host, "service", None)
    owner = getattr(getattr(service, "current_identity", None), "identity_id", None)
    account_epoch = getattr(service, "_account_context_epoch", None)
    revision = getattr(host, "_loan_cover_decision_revision", None)
    cache = getattr(host, "_loan_cover_decision_cache", None)
    trace = getattr(host, "_loan_cover_lifecycle", None)
    tokens = getattr(host, "_loan_cover_trace_tokens", None)
    if (owner != loan.identity_id or type(owner) is not str
            or type(account_epoch) is not int or account_epoch < 0
            or type(revision) is not int or revision < 1
            or type(cache) is not CoverDecisionCache
            or type(trace) is not CoverLifecycleTrace
            or type(tokens) is not OrderedDict):
        return None
    remembered = tokens.get(scope)
    if remembered is None:
        return None
    token, decision = remembered
    if (type(token) is not CoverCacheToken
            or type(decision) is not ResolvedBookCover
            or token.revision != revision
            or decision.scope != scope or decision.uri != expected_url
            or decision.status != "selected"
            or cache.peek(scope, revision=revision) is not decision):
        return None
    key = loan_source_key(loan)

    def applied() -> None:
        # This runs synchronously on the Gtk main context, after a decoded
        # texture was applied. A late callback must not read stale source data
        # or confirm a book detached from the current Loans model.
        if (getattr(host, "_closed", False)
                or getattr(getattr(service, "current_identity", None),
                           "identity_id", None) != owner
                or getattr(service, "_account_context_epoch", None) != account_epoch
                or getattr(host, "_loan_cover_decision_revision", None) != revision
                or getattr(host, "loan_objects", {}).get(key) is not loan
                or cache.peek(scope, revision=revision) is not decision):
            return
        covers = getattr(host, "covers", None)
        probe = getattr(covers, "probe_media", None)
        if not callable(probe):
            return
        # No URL, path or account is emitted to the public trace. A cache hit
        # must be physically validated before a GTK texture receipt is kept.
        try:
            result = probe(expected_url)
        except (OSError, ValueError):
            return
        if getattr(result, "source_cached", None) is not True:
            return
        if trace.note_media(
            token, source_cached=True,
            preview_cached=getattr(result, "preview_cached", None) is True,
        ):
            trace.note_painted(token, surface=surface)

    return applied


__all__ = [
    "subscribe_loan_cover_trace", "note_loan_cover_token",
    "loan_cover_paint_receipt",
]
