"""#4030: private admission of an exact local candidate for documentary search.

No supplier dispatch, notice write, right, declaration or attribution.
A historical provider origin/receipt is a veto even if not currently usable.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from .derived_variant_publication import (
    DerivedPublicationConflict, _verified_variant_file,
)

_MAX_BYTES = 1024 ** 3


def _text(value, limit=1024):
    return (type(value) is str and 0 < len(value) <= limit
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))


def _snapshot(storage, identity, placement_id):
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        row = db.execute(
            "SELECT p.artifact_id,p.path,p.state,a.sha256,a.size,i.directory_name "
            "FROM placements p JOIN artifacts a ON a.artifact_id=p.artifact_id "
            "JOIN identities i ON i.identity_id=p.identity_id "
            "WHERE p.placement_id=? AND p.identity_id=?",
            (placement_id, identity),
        ).fetchone()
        if row is None:
            return "scope_unverified", None
        artifact, path, state, digest, size, directory = row
        if (state != "present" or not _text(path, 4096)
                or not _text(digest, 64) or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)
                or artifact != "sha256:" + digest
                or type(size) is not int or size <= 0
                or not _text(directory) or directory in (".", "..")
                or "/" in directory or chr(92) in directory
                or db.execute(
                    "SELECT identity_id FROM identities WHERE directory_name=? LIMIT 2",
                    (directory,),
                ).fetchall() != [(identity,)]):
            return "scope_unverified", None
        # Do not use the empty return from a best-effort global projection
        # as negative evidence. Query the same exact owner/Artifact, including
        # malformed, historical or currently untrusted provider links.
        if db.execute(
            "SELECT 1 FROM origin_links WHERE identity_id=? AND artifact_id=? "
            "AND origin_kind IN ('provider','adept') LIMIT 1",
            (identity, artifact),
        ).fetchone():
            return "historical_origin_requires_preservation", None
        if db.execute(
            "SELECT 1 FROM acquisition_ledger WHERE identity_id=? AND artifact_id=? LIMIT 1",
            (identity, artifact),
        ).fetchone():
            return "historical_receipt_requires_review", None
        # Existing non-provider provenance can be displayed but never silently
        # replaced. The default preview conservatively excludes it.
        if db.execute(
            "SELECT 1 FROM origin_links WHERE identity_id=? AND artifact_id=? LIMIT 1",
            (identity, artifact),
        ).fetchone():
            return "other_origin_requires_review", None
        return "unlinked_snapshot", row


def preview_local_origin_suggestion(storage, *, library_root: Path,
                                    identity_id: str, placement_id: str):
    """Review one explicit private Placement, without any catalogue search.

    A positive review allows only a later separately admitted documentary
    query. It proves neither original supplier nor lack of historical rights.
    """
    def result(reason, verified=False):
        return {
            "status": "documentary_candidate" if verified else "excluded",
            "reason": reason,
            "physical_bytes_verified": verified,
            "provider_origin_attested_by_preview": False,
            "may_search_private_catalogues": False,
            "may_write_origin_or_right": False,
            "requires_separate_search_admission": True,
        }

    if (not _text(identity_id, 256) or not _text(placement_id, 256)
            or not isinstance(library_root, Path) or not library_root.is_absolute()
            or library_root == Path("/") or ".." in library_root.parts):
        return result("invalid_scope")
    try:
        status, row = _snapshot(storage, identity_id, placement_id)
        if row is None:
            return result(status)
        _, raw, _, digest, size, directory = row
        path = Path(raw)
        scope = library_root / directory
        if (not path.is_absolute() or ".." in path.parts or str(path) != raw
                or path == scope or not path.is_relative_to(scope)):
            return result("scope_unverified")
        if size > _MAX_BYTES:
            return result("review_budget_exceeded")
        with _verified_variant_file(path, digest, expected_size=size) as recheck:
            signature = recheck()
            if _snapshot(storage, identity_id, placement_id) != (status, row):
                return result("evidence_changed")
            if recheck() != signature:
                return result("evidence_changed")
        return result("no_same_scope_origin_observed", True)
    except (DerivedPublicationConflict, sqlite3.Error, OSError, RuntimeError,
            ValueError, TypeError, RecursionError):
        return result("evidence_unavailable")
