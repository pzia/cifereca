"""#7092 public projection of the existing bounded cache-only batch.

Not registered yet. Future host owns instance lifetime and calls revoke on
deactivation. Cancellation is cooperative, never a new network permission.
"""
from secrets import token_hex
from threading import Lock

from ..plugin_api.local_origin_review import (
    KnownOriginMatch, LocalOriginFileAdvice, LocalOriginBatchPage,
)
from .local_origin_suggestion_batch import admit_origin_batch, run_origin_batch
from .library_sqlite_freshness import sqlite_source_stamp


class LocalOriginBatchReadBrokerImpl:
    def __init__(self, service):
        self._service = service
        self._lock = Lock()
        self._generation = 0
        self._active = None
        self._revoked = False

    def _run(self, scope, cursor, stamp, generation, cancelled):
        def stop():
            with self._lock:
                stale = self._revoked or generation != self._generation
            return stale or (cancelled is not None and cancelled() is not False)

        try:
            if stop() or not scope.bound() or sqlite_source_stamp(scope.database) != stamp:
                return None
            result = run_origin_batch(self._service, scope, cursor=cursor, cancelled=stop)
            if (type(result) is not dict or result.get("status") != "partial"
                    or any(result.get(k) is not False for k in (
                        "coverage_complete", "provider_access_performed",
                        "association_authorized", "may_write_origin_or_right"))):
                return None
            following = result["next_cursor"]
            page = LocalOriginBatchPage(
                token=token_hex(32), page=result["page"],
                entries=tuple(LocalOriginFileAdvice(
                    file=r["file"], status=r["status"], reason=r["reason"],
                    known_matches=r["known_matches"], more_matches=r["more_matches"],
                    suggestions=tuple(KnownOriginMatch(*m) for m in r["suggestions"]),
                    c_window_status=r["c_window_status"],
                    unindexed_sources=r["unindexed_sources"],
                ) for r in result["entries"]),
                has_more=result["has_more"], can_continue=following is not None,
            )
            if stop() or not scope.bound() or sqlite_source_stamp(scope.database) != stamp:
                return None
            with self._lock:
                if self._revoked or generation != self._generation:
                    return None
                self._active = (scope, following, stamp, page.token)
                return page
        except Exception:
            return None

    def preview(self, *, identity_id, confirmed=False, cancelled=None, include_declared=False):
        if (confirmed is not True or type(identity_id) is not str
                or type(include_declared) is not bool
                or (cancelled is not None and not callable(cancelled))):
            return None
        with self._lock:
            if self._revoked:
                return None
            self._generation += 1
            generation = self._generation
            self._active = None
        try:
            scope = admit_origin_batch(self._service, confirmed=True,
                                       include_declared=include_declared)
            if scope is None or scope.request.identity_id != identity_id:
                return None
            stamp = sqlite_source_stamp(scope.database)
            if stamp is None:
                return None
            return self._run(scope, None, stamp, generation, cancelled)
        except Exception:
            return None

    def next_page(self, *, token, confirmed=False, cancelled=None):
        if (confirmed is not True or type(token) is not str
                or (cancelled is not None and not callable(cancelled))):
            return None
        with self._lock:
            if self._revoked or self._active is None:
                return None
            scope, cursor, stamp, expected = self._active
            if expected != token or cursor is None:
                return None
            self._active = None
            self._generation += 1
            generation = self._generation
        return self._run(scope, cursor, stamp, generation, cancelled)

    def cancel(self, *, token):
        with self._lock:
            if (type(token) is str and self._active is not None
                    and self._active[3] == token):
                self._active = None
                self._generation += 1

    def revoke(self):
        """Permanent revocation of this instance; reactivate with a fresh one."""
        with self._lock:
            self._revoked = True
            self._active = None
            self._generation += 1
