"""#4030: bounded read-only Maintenance batch of known-C comparisons.

The existing one-Placement verifier owns SHA/OPF/metadata and provenance
vetoes. A page is not an association, an absence proof or a live search.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from unicodedata import category

from .derived_variant_maintenance_review import admit_maintenance_review
from .library_sqlite_freshness import sqlite_source_stamp
from .local_origin_suggestion_operator import _binding_current, _root
from .local_origin_suggestion_preview import (
    ordinary_suggestion_path, suggest_local_placement_documents,
)

_PAGE = 4
_MAX_PAGES = 200
_MAX_BYTES = 128 * 1024 * 1024
_SECONDS = 3


@dataclass(frozen=True, slots=True, repr=False)
class BatchScope:
    request: object
    root: Path
    directory: str
    database: Path
    include_declared: bool = False

    def current(self):
        try:
            r = self.request
            return (r.current() and r.service.identity_by_id(r.identity_id) is r.owner
                    and r.owner.directory_name == self.directory
                    and _root(r.config) == self.root and Path(r.storage.path) == self.database)
        except Exception:
            return False

    def bound(self):
        # A disappeared SQLite connection or rebound profile is a scope veto,
        # including for callers probing this guard before scheduling a batch.
        try:
            r = self.request
            return (self.current() and _binding_current(r.storage, r.identity_id, self.directory))
        except Exception:
            return False


@dataclass(frozen=True, slots=True, repr=False)
class BatchCursor:
    scope: BatchScope
    stamp: tuple
    after: str
    page: int


def _token(value, limit=4096):
    """The batch cursor/identity selector must be one readable exact scope.

    Filename review already refuses hidden Unicode; admission and resumed
    SQLite pagination must not accept weaker, forged coordinates.
    """
    return (type(value) is str and 0 < len(value) <= limit
            and not any(category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                        for ch in value))


def admit_origin_batch(service, *, confirmed=False, include_declared=False):
    if confirmed is not True or type(include_declared) is not bool:
        return None
    try:
        request = admit_maintenance_review(service)
        if request is None:
            return None
        directory = request.owner.directory_name
        if (not _token(directory, 256) or directory in (".", "..")
                or "/" in directory or chr(92) in directory):
            return None
        scope = BatchScope(request, _root(request.config), directory,
                           Path(request.storage.path), include_declared)
        return scope if scope.current() else None
    except Exception:
        return None


def _rows(scope, after):
    """Only candidate coordinates, not a second provenance engine."""
    storage, owner = scope.request.storage, scope.request.identity_id
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise ValueError("origin_batch_nested_transaction")
        with db:
            db.execute("BEGIN")
            return tuple(db.execute(
                "SELECT p.placement_id,p.path,a.size FROM placements p "
                "JOIN artifacts a ON a.artifact_id=p.artifact_id "
                "WHERE p.identity_id=? AND p.state='present' AND p.placement_id>? "
                "AND NOT EXISTS(SELECT 1 FROM origin_links o WHERE "
                "o.identity_id=p.identity_id AND o.artifact_id=p.artifact_id) "
                "AND NOT EXISTS(SELECT 1 FROM acquisition_ledger l WHERE "
                "l.identity_id=p.identity_id AND l.artifact_id=p.artifact_id) "
                "ORDER BY p.placement_id LIMIT ?",
                (owner, after, _PAGE + 1),
            ).fetchall())


def run_origin_batch(service, scope, *, cursor=None, cancelled=None):
    """Explicit pages. Recheck SQLite and scope; never treat skip as no match.

    The byte/time budgets are cooperative between files, not hard I/O
    deadlines. A file skipped for budget remains unassessed.
    """
    if type(scope) is not BatchScope or scope.request.service is not service:
        return None

    def active():
        return scope.current() and (cancelled is None or cancelled() is False)

    try:
        if not active() or not scope.bound():
            return None
        stamp = sqlite_source_stamp(scope.database)
        if stamp is None:
            return None
        after, page = "", 1
        if cursor is not None:
            if (type(cursor) is not BatchCursor or cursor.scope is not scope
                    or cursor.stamp != stamp or not _token(cursor.after, 256)
                    or type(cursor.page) is not int or not 2 <= cursor.page <= _MAX_PAGES):
                return None
            after, page = cursor.after, cursor.page
        candidates = _rows(scope, after)
        if any(not _token(row[0], 256) for row in candidates):
            return None
        deadline = monotonic() + _SECONDS
        total = 0
        entries = []
        # Keep the exact page coordinates of a verification until delivery.
        # A later file review must not cause an earlier candidate to be
        # presented as still verified after its bytes or inode changed.
        observed = []
        owner = scope.request.identity_id
        for pid, raw, size in candidates[:_PAGE]:
            if not active() or sqlite_source_stamp(scope.database) != stamp:
                return None
            path = ordinary_suggestion_path(
                raw, library_root=scope.root, identity_id=owner, directory=scope.directory,
            )
            label = "Fichier non vérifié"
            result = None
            reason = "excluded_path"
            if path is not None:
                label = path.name[:255]
                reason = "review_budget"
                # Existing per-file verifier may rehash during rechecks:
                # reserve three file lengths conservatively for this page.
                if (type(size) is int and size > 0
                        and total + 3 * size <= _MAX_BYTES and monotonic() < deadline):
                    total += 3 * size
                    try:
                        before = path.stat(follow_symlinks=False)
                    except OSError:
                        before = None
                    result = suggest_local_placement_documents(
                        scope.request.storage, library_root=scope.root,
                        identity_id=owner, placement_id=pid,
                        explicitly_requested=True, page=0, page_size=5,
                        include_declared=scope.include_declared,
                    )
                    reason = result["reason"]
                    if result.get("suggestions") is not None:
                        if before is None:
                            return None
                        observed.append((path, (before.st_dev, before.st_ino, before.st_size,
                                                before.st_mtime_ns, before.st_ctime_ns)))
            suggestions = result.get("suggestions") if result else None
            entries.append({
                "file": label, "reason": reason,
                "status": "compared" if suggestions is not None else "not_assessed",
                "known_matches": suggestions.known_matches if suggestions is not None else None,
                "more_matches": suggestions.has_more if suggestions is not None else False,
                "suggestions": tuple((x.hit.title, x.hit.author, x.hit.provider_id,
                                      x.comparison, x.hit.stale,
                                      x.hit.record_id, x.hit.docbase,
                                      x.hit.edition_id, x.hit.medium,
                                      x.hit.media_format, x.hit.observed_at)
                                     for x in suggestions.suggestions)
                                if suggestions is not None else (),
            })
        if (not active() or not scope.bound() or sqlite_source_stamp(scope.database) != stamp
                or _rows(scope, after) != candidates):
            return None
        for path, expected in observed:
            if not active():
                return None
            info = path.stat(follow_symlinks=False)
            if (info.st_dev, info.st_ino, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns) != expected:
                return None
        if not active() or sqlite_source_stamp(scope.database) != stamp:
            return None
        more = len(candidates) > _PAGE
        following = (BatchCursor(scope, stamp, candidates[_PAGE - 1][0], page + 1)
                     if more and page < _MAX_PAGES else None)
        return {
            "status": "partial", "page": page, "entries": tuple(entries),
            "has_more": more, "next_cursor": following, "coverage_complete": False,
            "provider_access_performed": False, "association_authorized": False,
            "may_write_origin_or_right": False,
        }
    except Exception:
        return None
