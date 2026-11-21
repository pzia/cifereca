"""Core implementation of the #7087 read-only port; not yet registered.

One active page; no persistent plan, mutation or provider call. Host must
later bind the broker lifetime to the enabled contribution and revoke it
on deactivation. The native UI retains its existing path until migration.
"""
from dataclasses import replace
import secrets
import threading

from ..plugin_api.library_organizer import (
    OrganizerFileAdvice, OrganizerGroupAdvice, OrganizerPageAdvice,
)
from .library_organizer_review import (
    admit_organizer_review, run_organizer_review, select_organizer_keeper_advice,
)
from .library_sqlite_freshness import sqlite_source_stamp
from .canonical_keeper_review import review_canonical_keeper


class LibraryOrganizerReadBrokerImpl:
    def __init__(self, service):
        self._service = service
        self._lock = threading.Lock()
        self._active = None

    def _project(self, scope, result, stamp):
        if (type(result) is not dict or result.get("mutation_authorized") is not False
                or not scope.bound() or sqlite_source_stamp(scope.database) != stamp):
            return None
        page = OrganizerPageAdvice(
            token=secrets.token_hex(32), page=result["page"],
            placement_count=result["placement_count"], group_count=result["group_count"],
            groups=tuple(OrganizerGroupAdvice(
                reason=g["reason"], copies=g["copies"], category=g["category_advice"],
                category_evidence=g["category_evidence"], keeper=g["keeper"],
                files=tuple(OrganizerFileAdvice(
                    f["current"], f["proposed_name"], f["reason"],
                    f.get("classified_path_advice"), f.get("classified_path_reason"),
                ) for f in g["files"]),
            ) for g in result["groups"]),
            eligible=len(result["keeper_advice"].eligible), selected=0,
            has_more=result["next_cursor"] is not None,
            inventory_status=result["inventory_status"],
        )
        self._active = (scope, result, stamp, page)
        return page

    def _current(self, token):
        if type(token) is not str or self._active is None:
            return None
        scope, result, stamp, page = self._active
        if token != page.token:
            return None
        if not scope.bound() or sqlite_source_stamp(scope.database) != stamp:
            self._active = None
            return None
        return self._active

    def preview(self, *, identity_id, confirmed=False):
        if confirmed is not True or type(identity_id) is not str:
            return None
        with self._lock:
            self._active = None
            try:
                scope = admit_organizer_review(self._service)
                if scope is None or scope.identity != identity_id or not scope.bound():
                    return None
                stamp = sqlite_source_stamp(scope.database)
                if stamp is None:
                    return None
                return self._project(scope, run_organizer_review(self._service, scope), stamp)
            except Exception:
                return None

    def next_page(self, *, token, confirmed=False):
        if confirmed is not True:
            return None
        with self._lock:
            try:
                active = self._current(token)
                if active is None:
                    return None
                scope, result, stamp, page = active
                if not page.has_more:
                    return None
                self._active = None
                return self._project(scope, run_organizer_review(
                    self._service, scope, cursor=result["next_cursor"]), stamp)
            except Exception:
                self._active = None
                return None

    def select_all_advice(self, *, token, confirmed=False):
        if confirmed is not True:
            return None
        with self._lock:
            try:
                active = self._current(token)
                if active is None:
                    return None
                scope, result, stamp, page = active
                if not page.eligible or page.selected:
                    return None
                # SQLite's stamp cannot detect a replaced or missing file.
                # Refresh each previously eligible group's physical/dependency
                # evidence on this worker, without widening the reviewed page.
                eligible = {choice.artifact_id for choice in result["keeper_advice"].eligible}
                for review in result["keeper_reviews"]:
                    if review.artifact_id not in eligible:
                        continue
                    if (self._current(token) is None
                            or review_canonical_keeper(
                                scope.storage, library_root=scope.root,
                                identity_id=scope.identity, artifact_id=review.artifact_id,
                            ) != review):
                        self._active = None
                        return None
                selected = select_organizer_keeper_advice(scope, result, confirmed=True)
                if (selected is None or selected.executable is not False
                        or self._current(token) is None):
                    self._active = None
                    return None
                updated = replace(page, selected=len(selected.preselected))
                self._active = (scope, result, stamp, updated)
                return updated
            except Exception:
                self._active = None
                return None

    def cancel(self, *, token):
        with self._lock:
            if (type(token) is str and self._active is not None
                    and self._active[3].token == token):
                self._active = None

    def revoke(self):
        """Host-only lifetime boundary; not part of the public protocol."""
        with self._lock:
            self._active = None
