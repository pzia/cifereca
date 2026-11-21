"""#7092 exact, worker-only contextual review projected through a public port.

Private foundation, not registered until the contextual action cutover. The
existing Core operator is the sole owner of byte verification and C lookup.
An activation-scoped lease will additionally guard entry and result delivery.
"""
from secrets import token_hex
from threading import Lock

from ..plugin_api.local_origin_detail import LocalOriginDetailMatch, LocalOriginDetailPage
from .local_origin_documentary_suggestions import DocumentarySuggestionPage
from .local_origin_suggestion_operator import (
    admit_local_origin_suggestion, continue_local_origin_suggestion,
    run_local_origin_suggestion,
)


class LocalOriginDetailReadBrokerImpl:
    def __init__(self, service):
        self._service = service
        self._lock = Lock()
        self._generation = 0
        self._active = None
        self._revoked = False

    def _live(self, generation, view, request):
        with self._lock:
            if self._revoked or generation != self._generation:
                return False
        try:
            return view() is True and request.current() is True
        except Exception:
            return False

    def _read(self, request, view, generation):
        try:
            if not self._live(generation, view, request):
                return None
            result = run_local_origin_suggestion(self._service, request)
            if (type(result) is not dict or result.get("status") != "partial"
                    or any(result.get(flag) is not False for flag in (
                        "association_authorized", "may_write_origin_or_right",
                        "provider_access_performed"))):
                return None
            known = result.get("suggestions")
            if (type(known) is not DocumentarySuggestionPage
                    or known.page != request.page or known.page_size != request.page_size
                    or len(known.suggestions) > request.page_size
                    or any(flag is not False for flag in (
                        known.coverage_complete, known.provider_access_performed,
                        known.may_write_origin_or_right))):
                return None
            following = continue_local_origin_suggestion(self._service, request, result)
            if not self._live(generation, view, request):
                return None
            page = LocalOriginDetailPage(
                token_hex(32), known.page + 1,
                tuple(LocalOriginDetailMatch(
                    match.hit.provider_id, match.hit.record_id, match.hit.docbase,
                    match.hit.edition_id, match.hit.title, match.hit.author,
                    match.comparison, match.hit.stale, match.hit.observed_at,
                ) for match in known.suggestions),
                known.known_matches, known.has_more, following is not None,
                known.status, result["metadata_evidence"],
            )
            with self._lock:
                if self._revoked or self._generation != generation:
                    return None
                self._active = (page.token, following, view)
            if not self._live(generation, view, request):
                self.cancel(token=page.token)
                return None
            return page
        except Exception:
            return None

    def preview(self, *, identity_id, placement_id, still_current,
                confirmed=False, page_size=20):
        if (confirmed is not True or not callable(still_current)
                or type(page_size) is not int or not 1 <= page_size <= 20):
            return None
        with self._lock:
            if self._revoked:
                return None
            self._generation += 1
            generation = self._generation
            self._active = None
        try:
            if still_current() is not True:
                return None
            request = admit_local_origin_suggestion(
                self._service, identity_id=identity_id, placement_id=placement_id,
                confirmed=True, page_size=page_size,
            )
            if request is None:
                return None
            return self._read(request, still_current, generation)
        except Exception:
            return None

    def next_page(self, *, token, confirmed=False):
        if confirmed is not True or type(token) is not str:
            return None
        with self._lock:
            if self._revoked or self._active is None:
                return None
            expected, request, view = self._active
            if token != expected or request is None:
                return None
            self._active = None
            self._generation += 1
            generation = self._generation
        return self._read(request, view, generation)

    def cancel(self, *, token):
        with self._lock:
            if self._active is not None and type(token) is str and token == self._active[0]:
                self._active = None
                self._generation += 1

    def revoke(self):
        """Private host-only retirement; no I/O or waiting for file hashing."""
        with self._lock:
            self._revoked = True
            self._active = None
            self._generation += 1
