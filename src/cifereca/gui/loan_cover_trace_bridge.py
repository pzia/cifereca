"""Bounded N0 subscriptions for exact loan cover decisions (#6168).

The existing N1/N2 loan bridge already resolves the SAME scoped decision
for list and detail. Observe its real N2 events, without a synthetic
"painted" claim. CoverLifecycleTrace can later receive N3 validated bytes
and N4 texture-applied callbacks from CoreUI.

Window/identity/account/source contexts are captured separately from the
document identity. A stale A -> B -> A window never presents the previous
trace, even if the scoped Loan ID and cover URL are unchanged. This observer
is diagnostic-only; no provider I/O, rights changes, image download or GTK.
"""
from __future__ import annotations

from collections import OrderedDict

from ..core.book_cover_cache import CoverCacheToken, CoverDecisionCache, CoverSubscription
from ..core.book_cover_projection import ResolvedBookCover
from ..core.book_cover_projection import CoverDocumentScope
from ..core.book_cover_trace import CoverLifecycleTrace, CoverTraceSnapshot


class LoanCoverTraceSubscriptions:
    """One window-local bounded observer; called on the GTK owner thread."""

    def __init__(self, *, max_scopes: int = 128):
        if type(max_scopes) is not int or not 1 <= max_scopes <= 1024:
            raise ValueError("invalid loan cover observer limit")
        self._limit = max_scopes
        self._cache: CoverDecisionCache | None = None
        self._context: tuple[str, int | None, int | None] | None = None
        self._subscriptions: OrderedDict[
            CoverDocumentScope, CoverSubscription,
        ] = OrderedDict()
        self._trace = CoverLifecycleTrace(max_scopes=max_scopes)
        self._disposed = False
        self._tokens: OrderedDict[
            CoverDocumentScope, tuple[CoverCacheToken, ResolvedBookCover],
        ] = OrderedDict()

    @staticmethod
    def _valid_context(
        scope: CoverDocumentScope,
        context: tuple[object, object, object],
    ) -> bool:
        if (type(scope) is not CoverDocumentScope
                or type(context) is not tuple or len(context) != 3):
            return False
        identity, account_epoch, source_epoch = context
        return (
            type(identity) is str and identity == scope.identity_id
            and type(account_epoch) in (int, type(None))
            and (account_epoch is None or account_epoch >= 0)
            and type(source_epoch) in (int, type(None))
            and (source_epoch is None or source_epoch >= 0)
        )

    def _clear_subscriptions(self) -> None:
        for handle in self._subscriptions.values():
            handle.cancel()
        self._subscriptions.clear()
        self._tokens.clear()

    def watch(
        self, cache: CoverDecisionCache, scope: CoverDocumentScope, *,
        context: tuple[object, object, object],
    ) -> bool:
        """Attach before N2 begin/publish; refuse inconsistent source."""
        if self._disposed:
            return False
        if (type(cache) is not CoverDecisionCache
                or not self._valid_context(scope, context)):
            return False
        if self._cache is not cache or self._context != context:
            self._clear_subscriptions()
            self._cache = cache
            self._context = context
            # Old in-flight callbacks may reach their old trace object but
            # can never poison this new context's public N0 snapshot.
            self._trace = CoverLifecycleTrace(max_scopes=self._limit)
        if scope in self._subscriptions:
            self._subscriptions.move_to_end(scope)
            return True
        if len(self._subscriptions) == self._limit:
            old_scope, old = self._subscriptions.popitem(last=False)
            old.cancel()
            self._tokens.pop(old_scope, None)
        self._subscriptions[scope] = self._trace.attach(cache, scope)
        return True

    def snapshot(
        self, scope: CoverDocumentScope, *,
        context: tuple[object, object, object],
    ) -> CoverTraceSnapshot | None:
        if (self._disposed or self._context != context
                or not self._valid_context(scope, context)
                or scope not in self._subscriptions):
            return None
        return self._trace.snapshot(scope)

    def record_published(
        self, token: CoverCacheToken, decision: ResolvedBookCover, *,
        context: tuple[object, object, object],
    ) -> bool:
        """Remember the actual N2 CAS generation, not a synthetic revision."""
        if (type(token) is not CoverCacheToken
                or type(decision) is not ResolvedBookCover
                or not self._valid_context(token.scope, context)
                or self._disposed or self._context != context
                or self._cache is None or token.scope not in self._subscriptions
                or token.scope != decision.scope
                or token.revision != decision.revision
                or self._cache.peek(token.scope, revision=token.revision) is not decision):
            return False
        self._tokens[token.scope] = (token, decision)
        self._tokens.move_to_end(token.scope)
        return True

    def published_token(
        self, scope: CoverDocumentScope, *,
        context: tuple[object, object, object],
    ) -> tuple[CoverCacheToken, ResolvedBookCover] | None:
        """Only return a still-live selected N2 decision for this exact epoch."""
        if (self._disposed or self._context != context or self._cache is None
                or not self._valid_context(scope, context)
                or scope not in self._subscriptions):
            return None
        result = self._tokens.get(scope)
        if result is None:
            return None
        token, decision = result
        if (decision.status != "selected" or not decision.uri
                or self._cache.peek(scope, revision=token.revision) is not decision):
            return None
        self._tokens.move_to_end(scope)
        return result

    def observe_texture(
        self, token: CoverCacheToken, decision: ResolvedBookCover, *,
        context: tuple[object, object, object], surface: str,
        source_cached: bool, preview_cached: bool = False,
    ) -> bool:
        """Only a validated N3 source plus actual GTK receipt yields paint."""
        if surface not in ("list", "detail"):
            raise ValueError("invalid loan texture surface")
        if type(source_cached) is not bool or type(preview_cached) is not bool:
            raise TypeError("physical image observations must be booleans")
        current = self.published_token(token.scope, context=context)
        if (current is None or current[0] != token or current[1] is not decision):
            return False
        if not source_cached:
            self._trace.note_media(token, source_cached=False)
            return False
        if not self._trace.note_media(
            token, source_cached=True, preview_cached=preview_cached,
        ):
            return False
        return self._trace.note_painted(token, surface=surface)

    def stats(self) -> dict[str, int]:
        return {
            "subscriptions": len(self._subscriptions),
            "tracked_sources": self._trace.stats()["tracked_sources"],
        }

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._clear_subscriptions()
        self._cache = None
        self._context = None
        self._trace = CoverLifecycleTrace(max_scopes=self._limit)


__all__ = ["LoanCoverTraceSubscriptions"]
