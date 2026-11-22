"""#4030: private admission of an exact local candidate for documentary search.

No supplier dispatch, notice write, right, declaration or attribution.
A historical provider origin/receipt is a veto even if not currently usable.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .canonical_dedup_exclusions import ordinary_dedup_path_excluded

from .derived_variant_publication import (
    DerivedPublicationConflict, _verified_variant_file,
)

_MAX_BYTES = 1024 ** 3


def _text(value, limit=1024):
    return (type(value) is str and 0 < len(value) <= limit
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))


def ordinary_suggestion_path(raw, *, library_root, identity_id, directory):
    """Same candidate-path policy for individual and batch reviews.

    Path admissibility is only a veto. The subsequent pinned SHA and SQL
    observations still own physical and documentary admission.
    """
    if (not _text(raw, 4096) or not isinstance(library_root, Path)
            or not library_root.is_absolute() or library_root == Path("/")
            or ".." in library_root.parts or not _text(identity_id, 256)
            or not _text(directory, 256) or directory in (".", "..")
            or "/" in directory or chr(92) in directory):
        return None
    path = Path(raw)
    owner = library_root / directory
    if (not path.is_absolute() or str(path) != raw or ".." in path.parts
            or path == owner or not path.is_relative_to(owner)
            or any(part.startswith(".") for part in path.relative_to(owner).parts)
            or ordinary_dedup_path_excluded(
                path, library_root=library_root, identity_id=identity_id,
                owner_directory=directory)):
        return None
    return path


def _declared_witness(db, *, identity, path, inode, include_declared, digest, size,
                      signature=None):
    """Reuse #2797's strict decoder; never attach a stale declaration by path."""
    from .library_declared_links import DeclarationRejected, _records, _json

    try:
        matched = [
            row for row in _records(db)
            if row["state"] == "active" and (
                row["file"]["path"] == path
                or (inode is not None and tuple(row["file"]["fingerprint"][:2]) == inode))
        ]
    except DeclarationRejected:
        return "declaration_evidence_unavailable", None
    if not matched:
        return None, ()
    # A recorded path can be reused by a different book. Path/inode is a
    # discovery coordinate, not evidence that a user statement describes
    # the canonical Artifact currently verified at that location.
    if (len(matched) != 1 or matched[0]["scope"]["identity_id"] != identity
            or matched[0]["file"]["sha256"] != digest
            or matched[0]["file"]["size"] != size
            or (signature is not None and
                tuple(matched[0]["file"]["fingerprint"]) !=
                (signature[0], signature[1], signature[3],
                 signature[4], signature[5]))):
        return "declaration_scope_unverified", None
    if not include_declared:
        return "declared_origin_requires_review", None
    # Private exact witness; the public projection only gets a coarse label.
    return None, (_json(matched[0]),)


def _snapshot(storage, identity, placement_id, *, catalogue=False,
              include_declared=False, inode=None, signature=None):
    with storage._lock:
        db = storage._connect()
        # A consultation may be called from a borrowed connection. Refuse
        # before entering its context manager: our read must never commit
        # or roll back unrelated caller-owned work.
        if db.in_transaction:
            return "scope_unverified", None
        with db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT p.artifact_id,p.path,p.state,a.sha256,a.size,i.directory_name,a.media_format "
                "FROM placements p JOIN artifacts a ON a.artifact_id=p.artifact_id "
                "JOIN identities i ON i.identity_id=p.identity_id "
                "WHERE p.placement_id=? AND p.identity_id=?",
                (placement_id, identity),
            ).fetchone()
            if row is None:
                return "scope_unverified", None
            artifact, path, state, digest, size, directory, media_format = row
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
            reason, declaration = _declared_witness(
                db, identity=identity, path=path, inode=inode,
                include_declared=include_declared, digest=digest, size=size,
                signature=signature,
            )
            if declaration is None:
                return reason, None
            row = (*row, declaration)
            if catalogue:
                entries = db.execute(
                    "SELECT catalog_entry_id,artifact_id,identity_id,local_state,"
                    "CASE WHEN title IS NULL OR (typeof(title)='text' "
                    "AND length(CAST(title AS BLOB))<=2048) THEN title ELSE X'00' END,"
                    "CASE WHEN author IS NULL OR (typeof(author)='text' "
                    "AND length(CAST(author AS BLOB))<=2048) THEN author ELSE X'00' END,"
                    "provider_id "
                    "FROM catalog_entries WHERE placement_id=? ORDER BY catalog_entry_id LIMIT 33",
                    (placement_id,),
                ).fetchall()
                if len(entries) > 32 or (not entries and media_format not in ('epub', 'kepub')):
                    return "catalogue_evidence_unavailable", None
                # All claims about this exact placement must agree. Do not pick
                # a convenient title from an ambiguous or foreign catalogue row.
                if any(entry[1:4] != (artifact, identity, "local")
                       or entry[6] != "local" for entry in entries):
                    return "catalogue_evidence_unavailable", None
                # An oversized claim is an invalid BLOB sentinel, never a missing value.
                # SQLite's TEXT affinity does not guarantee a native TEXT
                # value: BLOB/integer titles or authors are corrupt local
                # catalogue evidence, even when the EPUB OPF is independently
                # readable. Never silently bless an inconsistent catalogue.
                if any((entry[4] is not None and type(entry[4]) is not str)
                       or (entry[5] is not None and type(entry[5]) is not str)
                       for entry in entries):
                    return "catalogue_evidence_unavailable", None
                descriptions = {(entry[4], entry[5]) for entry in entries}
                if len(descriptions) > 1:
                    return "catalogue_evidence_ambiguous", None
                title, author = next(iter(descriptions)) if descriptions else (None, None)
                if media_format not in ("epub", "kepub"):
                    if not _text(title, 160) or title.strip() == "Sans titre":
                        return "catalogue_title_unavailable", None
                    if author is not None and not _text(author, 512):
                        return "catalogue_evidence_unavailable", None
                row = (*row, tuple(entries), title, author)
            return "unlinked_snapshot", row


class _CandidateRejected(ValueError):
    pass


@contextmanager
def _verified_candidate(storage, *, library_root, identity_id, placement_id,
                        catalogue=False, include_declared=False):
    if (type(include_declared) is not bool or not _text(identity_id, 256) or not _text(placement_id, 256)
            or not isinstance(library_root, Path) or not library_root.is_absolute()
            or library_root == Path("/") or ".." in library_root.parts):
        raise _CandidateRejected("invalid_scope")

    def snapshot(signature=None):
        return _snapshot(storage, identity_id, placement_id, catalogue=catalogue,
                         include_declared=include_declared,
                         inode=signature[:2] if signature is not None else None,
                         signature=signature)

    status, row = snapshot()
    if row is None:
        raise _CandidateRejected(status)
    _, raw, _, digest, size, directory = row[:6]
    path = ordinary_suggestion_path(
        raw, library_root=library_root, identity_id=identity_id, directory=directory,
    )
    if path is None:
        raise _CandidateRejected("scope_unverified")
    if size > _MAX_BYTES:
        raise _CandidateRejected("review_budget_exceeded")
    with _verified_variant_file(path, digest, expected_size=size) as recheck:
        signature = recheck()
        checked_status, checked = snapshot(signature)
        if checked is None:
            # An origin/receipt appearing after the first snapshot, or any
            # other lost SQL admission, is a change of evidence. A previously
            # unseen inode alias is the only expected additional declaration veto.
            raise _CandidateRejected(checked_status if checked_status in (
                "declared_origin_requires_review", "declaration_scope_unverified",
            ) else "evidence_changed")
        # An alias can be discovered only after opening the verified inode.
        # Every other pinned SQL coordinate must still match the initial read.
        if checked[:7] != row[:7] or checked[8:] != row[8:]:
            raise _CandidateRejected("evidence_changed")
        if row[7] and checked[7] != row[7]:
            raise _CandidateRejected("evidence_changed")
        status, row = checked_status, checked

        def current():
            if snapshot(signature) != (status, row) or recheck() != signature:
                raise _CandidateRejected("evidence_changed")

        current()
        yield row, path, signature
        current()


def preview_local_origin_suggestion(storage, *, library_root: Path,
                                    identity_id: str, placement_id: str,
                                    include_declared=False):
    """Review one explicit private Placement, without any catalogue search."""
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

    try:
        with _verified_candidate(storage, library_root=library_root,
                                 identity_id=identity_id, placement_id=placement_id,
                                 include_declared=include_declared) as (row, _path, _signature):
            reason = "declared_unverified" if row[7] else "no_same_scope_origin_observed"
        return result(reason, True)
    except _CandidateRejected as exc:
        return result(str(exc))
    except (DerivedPublicationConflict, sqlite3.Error, OSError, RuntimeError,
            ValueError, TypeError, RecursionError):
        return result("evidence_unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class _SuggestionContinuation:
    """Private observation, no durable cursor or authorization."""
    storage: object
    root: Path
    identity: str
    placement: str
    evidence: tuple
    signature: tuple
    title: str
    author: str | None
    local_isbns: tuple[str, ...]
    next_page: int
    page_size: int


@dataclass(frozen=True, slots=True, repr=False)
class _PublicComparisonEvidence:
    """Private witness of the exact file reviewed before a title may be sent."""
    storage: object
    root: Path
    identity: str
    placement: str
    evidence: tuple
    signature: tuple

    def matches_scope(self, request):
        return (self.storage is request.storage and self.root == request.root
                and self.identity == request.identity_id
                and self.placement == request.placement_id)


def suggest_local_placement_documents(storage, *, library_root: Path,
                                     identity_id: str, placement_id: str,
                                     explicitly_requested: bool = False,
                                     page: int = 0, page_size: int = 20,
                                     continuation=None, include_declared=False):
    """Private one-shot controller for known C, not a live catalogue search.

    The controller must pass the admitted identity and configured root, never
    a plugin-selected path. Catalogue text is existing local descriptive evidence only; EPUB text
    comes from the verified inode. Neither is source proof.
    Revoke the entire page if scope, bytes or catalogue claims change.
    """
    from .local_origin_documentary_suggestions import suggest_known_public_documents

    def result(reason, suggestions=None, *, metadata="local_catalogue_only", next_page=None,
               local_isbns=(), public_evidence=None):
        return {
            "status": "partial" if suggestions is not None else "excluded",
            "reason": reason,
            "suggestions": suggestions,
            "continuation": next_page,
            "public_comparison_evidence": public_evidence if suggestions is not None else None,
            "metadata_evidence": metadata if suggestions is not None else "not_checked",
            "local_isbns": local_isbns if suggestions is not None else (),
            "isbn_catalogue_comparison_performed": False,
            "association_authorized": False,
            "may_write_origin_or_right": False,
            "provider_access_performed": False,
        }

    if explicitly_requested is not True or type(include_declared) is not bool:
        return result("explicit_request_required")
    if (type(page) is not int or not 0 <= page <= 200
            or type(page_size) is not int or not 1 <= page_size <= 50):
        return result("invalid_page")
    if ((page == 0 and continuation is not None)
            or (page > 0 and (
                type(continuation) is not _SuggestionContinuation
                or continuation.storage is not storage
                or continuation.root != library_root
                or continuation.identity != identity_id
                or continuation.placement != placement_id
                or continuation.next_page != page
                or continuation.page_size != page_size))):
        return result("continuation_unverified")
    try:
        with _verified_candidate(storage, library_root=library_root,
                                 identity_id=identity_id, placement_id=placement_id,
                                 catalogue=True, include_declared=include_declared) as candidate:
            local, path, signature = candidate
            title, author = local[-2:]
            metadata = "local_catalogue_only"
            local_isbns = ()
            if local[6] in ("epub", "kepub"):
                from .local_origin_epub_evidence import read_verified_epub_bibliography
                observation = read_verified_epub_bibliography(path, signature)
                if observation is None:
                    raise _CandidateRejected("epub_description_unverified")
                title, authors, local_isbns = observation
                author = authors[0] if len(authors) == 1 else None
                metadata = "verified_epub_opf_description"
            # Fresh SHA, inode and descriptive evidence must match the
            # preceding page. C itself is re-read independently: its result
            # set may change and no cross-page snapshot is claimed.
            if continuation is not None and (
                continuation.evidence != local
                or continuation.signature != signature
                or (continuation.title, continuation.author) != (title, author)
                or continuation.local_isbns != local_isbns
            ):
                raise _CandidateRejected("continuation_evidence_changed")
            suggestions = suggest_known_public_documents(
                storage, title=title, author=author,
                page=page, page_size=page_size,
            )
            following = (_SuggestionContinuation(
                storage, library_root, identity_id, placement_id, local,
                signature, title, author, local_isbns, page + 1, page_size,
            ) if suggestions.has_more and page < 200 else None)
            public_evidence = (_PublicComparisonEvidence(
                storage, library_root, identity_id, placement_id, local, signature,
            ) if page == 0 and metadata == "verified_epub_opf_description" and not local[7] else None)
        return result("declared_unverified_comparison" if local[7] else "documentary_comparison_only", suggestions,
                      metadata=metadata, next_page=following, local_isbns=local_isbns,
                      public_evidence=public_evidence)
    except _CandidateRejected as exc:
        return result(str(exc))
    except (DerivedPublicationConflict, sqlite3.Error, OSError, RuntimeError,
            ValueError, TypeError, RecursionError):
        return result("evidence_unavailable")
