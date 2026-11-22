"""#4520: explicitly confirmed recovery of an already linked derivative.

Core-only, never called from startup or from a plugin. No relinking, new
transformation, deletion, provider operation or permission from a SHA alone.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
from threading import get_ident

from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from .derived_variant_journal import (
    DerivedJournalConflict, DerivedReservation, _digest, _read_pending_reservation,
)
from .derived_variant_publication import (
    DerivedPublicationConflict, _file_signature, _verified_variant_file,
    publish_derived_variant,
)
from .library_inventory import _open_root

_MAX_BYTES = 1024 ** 3


def _lock_current(lock):
    if type(lock) is not InterProcessLock:
        return False
    try:
        if lock._existing_nofollow:
            return (
                lock.path == acquisition_lock_file(create=False)
                and lock.strict_current()
            )
        held = lock._lease
        if (held is None or lock._handle is None or held.handle.closed
                or held.pid != os.getpid() or held.thread_id != get_ident()
                or os.path.abspath(lock.path) != os.path.abspath(acquisition_lock_file())):
            return False
        named = lock.path.lstat()
        actual = os.fstat(held.handle.fileno())
        return (stat.S_ISREG(named.st_mode) and
                (named.st_dev, named.st_ino) == (actual.st_dev, actual.st_ino)
                == (held.device, held.inode))
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def _exact_scope(db, *, identity_id, root, intent):
    profiles = db.execute(
        "SELECT directory_name FROM identities WHERE identity_id=? LIMIT 2",
        (identity_id,),
    ).fetchall()
    if len(profiles) != 1 or intent.identity_id != identity_id:
        return False
    directory = profiles[0][0]
    if (type(directory) is not str or not directory or directory in (".", "..")
            or "/" in directory or chr(92) in directory
            or db.execute(
                "SELECT identity_id FROM identities WHERE directory_name=? LIMIT 2",
                (directory,),
            ).fetchall() != [(identity_id,)]):
        return False
    profile = root / directory
    if not all(Path(path).is_relative_to(profile) and Path(path) != profile
               for path in (intent.parent_path, intent.staged_path, intent.candidate_path)):
        return False
    parent_id = "sha256:" + intent.parent_sha256
    return (
        db.execute(
            "SELECT artifact_id,identity_id,path,state FROM placements WHERE placement_id=?",
            (intent.parent_placement_id,),
        ).fetchone() == (parent_id, identity_id, intent.parent_path, "present")
        and db.execute(
            "SELECT sha256,size FROM artifacts WHERE artifact_id=?", (parent_id,),
        ).fetchone() == (intent.parent_sha256, intent.parent_stamp[2])
    )


def _require_link(stage, candidate, signature, *, flush=False):
    """Both names must still designate the exact regular, pinned inode."""
    stage_dir = target_dir = fd = None
    try:
        stage_dir = _open_root(stage.parent)
        target_dir = _open_root(candidate.parent)
        fd = os.open(stage.name, os.O_RDONLY | os.O_NOFOLLOW |
                     os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=stage_dir)

        def check():
            infos = (os.fstat(fd),
                     os.stat(stage.name, dir_fd=stage_dir, follow_symlinks=False),
                     os.stat(candidate.name, dir_fd=target_dir, follow_symlinks=False))
            if any(not stat.S_ISREG(info.st_mode) or _file_signature(info) != signature
                   for info in infos):
                raise DerivedJournalConflict("derived_recovery_link_unverified")

        check()
        if flush:
            os.fsync(fd)
            os.fsync(stage_dir)
            os.fsync(target_dir)
            check()
    except OSError:
        raise DerivedJournalConflict("derived_recovery_link_unverified") from None
    finally:
        for handle in (fd, target_dir, stage_dir):
            if handle is not None:
                os.close(handle)


def recover_linked_derived_publication(
    storage, *, library_root: Path, identity_id: str,
    reservation: DerivedReservation, approved_receipt_sha256: str,
    shared_lock: InterProcessLock, scope_current, _private_guard=None,
):
    """Commit one already linked pending receipt after fresh explicit approval.

    The controller must hold the shared acquisition lock, supply its configured
    root and a live transaction-safe identity/epoch guard. A pending receipt
    with no link is deliberately not recovered here. The original transform's
    warnings/facts were not journaled: never reconstruct them from the file.
    Repeated approval after completion refuses the missing pending receipt.
    """
    if (type(reservation) is not DerivedReservation
            or not _digest(approved_receipt_sha256)
            or approved_receipt_sha256 != reservation.receipt_sha256
            or not isinstance(library_root, Path) or not library_root.is_absolute()
            or library_root == Path("/") or ".." in library_root.parts
            or type(identity_id) is not str or not identity_id
            or not callable(scope_current) or not _lock_current(shared_lock)):
        raise DerivedJournalConflict("derived_recovery_approval_required")

    private_options = {"_private_guard": _private_guard} if _private_guard is not None else {}

    def live(db):
        return _lock_current(shared_lock) and scope_current(db) is True

    with storage._lock:
        db = storage._connect()
        # Neither an unsuccessful BEGIN nor a read-only guard may settle
        # a transaction belonging to the caller.
        if db.in_transaction:
            raise DerivedJournalConflict("derived_recovery_nested_transaction")
        with db:
            db.execute("BEGIN")
            if not live(db):
                raise DerivedJournalConflict("derived_recovery_scope_changed")
            _raw, intent = _read_pending_reservation(db, reservation, **private_options)
            if not _exact_scope(db, identity_id=identity_id, root=library_root, intent=intent):
                raise DerivedJournalConflict("derived_recovery_scope_changed")
            if db.execute("SELECT 1 FROM placements WHERE path=? LIMIT 1",
                          (intent.candidate_path,)).fetchone():
                raise DerivedJournalConflict("derived_recovery_target_claimed")
    if intent.parent_stamp[2] + intent.output_size > _MAX_BYTES:
        raise DerivedJournalConflict("derived_recovery_budget_exceeded")

    stage, candidate = Path(intent.staged_path), Path(intent.candidate_path)
    with _verified_variant_file(stage, intent.output_sha256,
                                expected_size=intent.output_size) as stage_current:
        signature = stage_current()
        if (signature[0], signature[1], *signature[3:5]) != intent.output_stamp:
            raise DerivedJournalConflict("derived_recovery_link_unverified")
        _require_link(stage, candidate, signature, flush=True)

        def transaction_current(db):
            if not live(db):
                return False
            try:
                _raw, observed = _read_pending_reservation(db, reservation, **private_options)
                if (observed != intent or
                        not _exact_scope(db, identity_id=identity_id,
                                         root=library_root, intent=intent)
                        or stage_current() != signature):
                    return False
                _require_link(stage, candidate, signature)
                return True
            except (DerivedJournalConflict, DerivedPublicationConflict, OSError):
                return False

        # The common writer checks the original parent's full hash and inode,
        # the output and exact receipt twice, and commits B and the journal
        # transition in the same FULL SQLite transaction.
        publish_derived_variant(
            storage, parent_artifact_id="sha256:" + intent.parent_sha256,
            parent_sha256=intent.parent_sha256,
            parent_placement_id=intent.parent_placement_id,
            parent_path=Path(intent.parent_path), identity_id=identity_id,
            published_path=candidate, output_sha256=intent.output_sha256,
            output_size=intent.output_size, media_format=intent.media_format,
            mime_type="application/pdf" if intent.media_format == "pdf" else "application/epub+zip",
            variant_kind=intent.variant_kind, transformer_id=intent.transformer_id,
            transformer_version=intent.transformer_version,
            profile_id=intent.profile_id or None, profile_version=intent.profile_version,
            options_fingerprint=intent.options_fingerprint, book_id=None,
            warnings=(), facts={"recovered_from_private_intent": True,
                                "transformer_facts_available": False},
            scope_current=transaction_current, journal_reservation=reservation,
            **private_options,
        )
    return {"status": "committed", "destructive_cleanup_allowed": False}
