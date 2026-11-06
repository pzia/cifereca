"""P0 #3196: strict read-only review of one old backup's canonical publication.

A legacy backup_items.complete row, its SHA or a matching title never
proves physical presence or provenance. This inspector admits only an
already-published canonical B Placement with a full completed path receipt
and current exact Kobo account/source proof. No SQL/FS mutations or GETs.

"verified_existing_publication" is an ephemeral observation, NOT a migration
token, entitlement, right, skip-download decision or deletion permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import ExitStack
import os
from pathlib import Path
import sqlite3
import stat
from typing import Literal

from .canonical_legacy_scope_review import _completed_path_receipt_evidence
from .canonical_publication import _same_file
from .convergence_verified_presence import _verified_copy
from .missing_placement_retirement import _directory_signature


@dataclass(frozen=True, slots=True)
class LegacyBackupPublicationReview:
    status: Literal["blocked", "verified_existing_publication"]
    reason: str
    read_only: bool = True

    @property
    def may_migrate_or_retire(self) -> bool:
        return False


class _Refuse(ValueError):
    pass


def _require(ok, reason):
    if not ok:
        raise _Refuse(reason)


def review_legacy_backup_publication(
    storage, *, library_root: Path,
    identity_id: str, credential_id: str, item_id: str,
    _db: sqlite3.Connection | None = None,
) -> LegacyBackupPublicationReview:
    """Qualify existing canonical publication of one old Kobo backup row.

    Nothing is written or prepared for later replay. A caller must never
    cache this result as a future write authorization: the file, account,
    mount and received source can change immediately after this review.
    """
    def blocked(code):
        return LegacyBackupPublicationReview("blocked", code)

    if (not isinstance(library_root, Path) or not library_root.is_absolute()
            or any(type(value) is not str or not 0 < len(value) <= 1024
                   or any(ord(char) < 32 or ord(char) == 127 for char in value)
                   for value in (identity_id, credential_id, item_id))):
        return blocked("invalid_request")
    try:
        if _db is None and storage.schema_version() < 14:
            return blocked("schema_unavailable")
        # The maintenance cutover can supply its ALREADY locked, BEGIN IMMEDIATE
        # transaction. Reuse every physical/SourceKey/receipt test inside that
        # transaction; a separate read connection would permit an intervening
        # canonical rebind between review and archive retirement.
        with ExitStack() as context:
            if _db is None:
                context.enter_context(storage._lock)
                db = context.enter_context(storage._connect())
                db.execute("BEGIN")
            else:
                db = _db
            row = db.execute(
                "SELECT identity_id,account_id,revision_id,status,local_path,"
                "sha256,size FROM backup_items WHERE provider_id='kobo' "
                "AND credential_id=? AND item_id=?",
                (credential_id, item_id),
            ).fetchone()
            _require(row is not None, "backup_missing")
            owner, account, revision, status, path, digest, size = row
            _require(owner == identity_id and type(account) is str,
                     "account_scope_unverified")
            _require(type(revision) is str and bool(revision),
                     "revision_unverified")
            _require(status == "complete", "backup_not_complete")
            _require(type(path) is str and bool(path)
                     and type(digest) is str and len(digest) == 64
                     and all(ch in "0123456789abcdef" for ch in digest)
                     and type(size) is int and size > 0,
                     "backup_file_evidence_unverified")
            _require(db.execute(
                "SELECT COUNT(*) FROM backup_items WHERE local_path=?",
                (path,),
            ).fetchone()[0] == 1, "shared_backup_path")

            # Credential is globally unique by schema. Require its registered
            # current identity, enabled state and the account on that SAME
            # connection (not merely an account with the matching label).
            connection = db.execute(
                "SELECT connection_id,identity_id,enabled "
                "FROM provider_connections WHERE provider_id='kobo' "
                "AND credential_id=?",
                (credential_id,),
            ).fetchone()
            _require(connection is not None
                     and connection[1:] == (identity_id, 1),
                     "connection_unverified")
            _require(db.execute(
                "SELECT COUNT(*) FROM provider_accounts WHERE "
                "connection_id=? AND identity_id=? AND provider_id='kobo' "
                "AND credential_id=? AND account_id=?",
                (connection[0], identity_id, credential_id, account),
            ).fetchone()[0] == 1, "borrower_unverified")

            placement = db.execute(
                "SELECT identity_id,artifact_id,state,provider_id,"
                "source_item_id FROM placements WHERE path=?",
                (path,),
            ).fetchone()
            _require(placement is not None
                     and placement == (
                         identity_id, "sha256:" + digest, "present", None, None,
                     ), "canonical_placement_unverified")
            artifact = db.execute(
                "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
                (placement[1],),
            ).fetchone()
            _require(artifact == (digest, size),
                     "artifact_unverified")
            directory = db.execute(
                "SELECT directory_name FROM identities WHERE identity_id=?",
                (identity_id,),
            ).fetchone()
            _require(directory is not None and type(directory[0]) is str
                     and directory[0] not in ("", ".", "..")
                     and "/" not in directory[0] and "\\" not in directory[0]
                     and db.execute(
                         "SELECT COUNT(*) FROM identities "
                         "WHERE directory_name=?",
                         (directory[0],),
                     ).fetchone()[0] == 1, "profile_unverified")
            folder = directory[0]

            # Pin root+identity directory alongside the file: a same-hash
            # hardlink after a profile mount swap must fail closed.
            try:
                root_stat = os.stat(library_root, follow_symlinks=False)
                profile_signature = _directory_signature(library_root / folder)
                before = os.stat(path, follow_symlinks=False)
            except (OSError, ValueError) as error:
                raise _Refuse("physical_unverified") from error
            _require(stat.S_ISDIR(root_stat.st_mode)
                     and profile_signature is not None
                     and stat.S_ISREG(before.st_mode),
                     "physical_unverified")
            _require(_verified_copy(
                path, root=library_root, owner_directory=folder,
                registered_directories=frozenset((folder,)),
                size=size, sha256=digest,
            ), "physical_unverified")
            _require(_same_file(Path(path), before),
                     "physical_changed")

            # A published canonical source must be independently attested
            # by its exact durable stage/SourceKey/OriginLink/borrower/path.
            # This helper checks the whole bounded ledger for collisions
            # with this physical path across every profile and account.
            try:
                receipt_count = _completed_path_receipt_evidence(
                    db, path=path, identity=identity_id,
                    artifact=placement[1], provider="kobo", edition=item_id,
                    credential=credential_id, account=account,
                )
            except (ValueError, TypeError, sqlite3.Error) as error:
                raise _Refuse("canonical_receipt_unverified") from error
            _require(receipt_count == 1, "canonical_receipt_unverified")

            after_root = os.stat(library_root, follow_symlinks=False)
            _require((root_stat.st_dev, root_stat.st_ino)
                     == (after_root.st_dev, after_root.st_ino)
                     and _directory_signature(library_root / folder)
                     == profile_signature
                     and _same_file(Path(path), before),
                     "physical_changed")
            return LegacyBackupPublicationReview(
                "verified_existing_publication",
                "exact_canonical_source_and_bytes_observed",
            )
    except _Refuse as error:
        return blocked(str(error))
    except (sqlite3.Error, OSError, TypeError, ValueError, RuntimeError,
            RecursionError):
        return blocked("inspection_unavailable")


__all__ = ["LegacyBackupPublicationReview", "review_legacy_backup_publication"]
