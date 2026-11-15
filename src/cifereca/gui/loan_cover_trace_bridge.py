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

from ..core.book_cover_cache import CoverDecisionCache, CoverSubscription
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
            _, old = self._subscriptions.popitem(last=False)
            old.cancel()
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

    def record_applied_texture(
        self, cache: CoverDecisionCache, scope: CoverDocumentScope, *,
        context: tuple[object, object, object], revision: int,
        uri: str, surface: str, loader: object,
    ) -> bool:
        """Record N3 physical bytes + N4 real GTK paint for a live N2 lease.

        ONLY call from CoverView's post-Gdk.Texture.set_paintable callback;
        a cache hit, resolved decision or a loader callback is not proof of
        actual painting. This method rechecks source, revision, URI,
        observation generation and GTK host epochs independently. A stale
        callback never borrows another profile's image or makes a network
        request. The N3 probe observes *existing* validated cache files.
        """
        if surface not in ("list", "detail"):
            raise ValueError("invalid cover surface")
        if (self._disposed or self._cache is not cache
                or self._context != context
                or not self._valid_context(scope, context)
                or scope not in self._subscriptions
                or type(revision) is not int or revision < 0
                or type(uri) is not str or not uri):
            return False
        trace = self._trace
        state = trace.snapshot(scope)
        if (state is None or state.status != "selected"
                or not state.resolved or state.revision != revision
                or state.invalidated):
            return False
        token = cache.selected_token(scope, revision=revision, uri=uri)
        if token is None or state.generation != token.generation:
            return False
        from .cover_loader import CoverMediaState
        probe = getattr(loader, "probe_media", None)
        if not callable(probe):
            return False
        try:
            media = probe(uri)
        except (OSError, TypeError, ValueError):
            return False
        if type(media) is not CoverMediaState or not media.source_cached:
            return False
        if not trace.note_media(
            token, source_cached=True, preview_cached=media.preview_cached,
        ):
            return False
        return trace.note_painted(token, surface=surface)

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
