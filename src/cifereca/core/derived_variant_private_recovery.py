"""#6937 isolated v3 recovery: exact registered region and linked names.

No plugin port, deletion or production activation. The common
publication transaction still owns B and the pending/committed transition.
"""
from contextlib import ExitStack
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat

from . import derived_variant_journal as journal
from . import derived_variant_private_region as region
from .derived_variant_link import _hash, _regular, _require_absent, _require_named
from .derived_variant_supervised_recovery import (
    _exact_scope, _lock_current, recover_linked_derived_publication,
)
from .library_inventory import _open_root

_MAX_BYTES = 1024 ** 3


@dataclass(frozen=True, slots=True, repr=False)
class _PrivateRecoveryGuard:
    storage: object
    root: Path
    identity_id: str
    reservation: journal.DerivedReservation
    binding_sha256: str
    lock: object
    scope_current: object

    def verify(self, db, reservation, intent, record):
        return self._check(db, reservation, intent, record, create_link=False)

    def link(self, db, reservation, intent, record):
        return self._check(db, reservation, intent, record, create_link=True)

    def _check(self, db, reservation, intent, record, *, create_link):
        def require(ok):
            if not ok:
                raise journal.DerivedJournalConflict("derived_private_recovery_unverified")

        try:
            require(db.in_transaction and db is self.storage._connect()
                    and reservation == self.reservation
                    and record["schema"] == journal._PRIVATE_SCHEMA
                    and record["region_binding_sha256"] == self.binding_sha256
                    and intent.identity_id == self.identity_id
                    and intent.parent_stamp[2] + intent.output_size <= _MAX_BYTES)

            def live():
                require(_lock_current(self.lock) and self.scope_current(db) is True
                        and _exact_scope(db, identity_id=self.identity_id,
                                         root=self.root, intent=intent))

            live()
            directory = region._directory(db, self.identity_id)
            parent, stage, final = map(Path, (
                intent.parent_path, intent.staged_path, intent.candidate_path))
            require(stage.parent == self.root / directory / region._REGION)
            key = region._key(self.identity_id)
            binding = region._read_record(db, key)
            require(binding is not None and journal._receipt_digest(binding) == self.binding_sha256)
            owner = json.loads(binding, object_pairs_hook=journal._unique_pairs)

            def check_region():
                live()
                require(region._read_record(db, key) == binding)
                verdict = region._ensure_region(
                    db, storage=self.storage, root=self.root,
                    identity_id=self.identity_id, placement_id=intent.parent_placement_id,
                    shared_lock=self.lock, scope_current=self.scope_current, provision=False)
                require(verdict["status"] == "verified")

            check_region()
            # Both names must be the same recorded inode and the ONLY two
            # links. Mode/owner and mount ID are checked, not just st_dev.
            # The common writer hashes parent and final; the recovery loop
            # additionally hashes stage and fsyncs both links before commit.
            with ExitStack() as stack:
                def opened(path):
                    folder = _open_root(path.parent)
                    stack.callback(os.close, folder)
                    fd = os.open(path.name, region._FLAGS, dir_fd=folder)
                    stack.callback(os.close, fd)
                    return folder, fd

                stage_dir, stage_fd = opened(stage)
                parent_dir, parent_fd = opened(parent)
                output = _regular(stage_fd)
                original = _regular(parent_fd)
                mount = region._mount_id(stage_dir)

                def names(*, linked):
                    live()
                    require(original == intent.parent_stamp
                            and output[:4] == intent.output_stamp
                            and output[:2] != original[:2]
                            and list(region._private_directory(stage_dir, exact=True))
                                == owner["region_stamp"])
                    handles = ((stage, stage_dir, stage_fd, output),
                               (parent, parent_dir, parent_fd, original))
                    if linked:
                        require(_regular(final_fd) == output)
                        handles += ((final, final_dir, final_fd, output),)
                    for path, folder, fd, expected in handles:
                        _require_named(path, folder, fd, expected)
                        require(region._mount_id(folder) == region._mount_id(fd) == mount)
                        fresh_dir = _open_root(path.parent)
                        try:
                            fresh_fd = os.open(path.name, region._FLAGS, dir_fd=fresh_dir)
                            try:
                                require(region._mount_id(fresh_dir) == region._mount_id(fresh_fd)
                                        == mount and _regular(fresh_fd) == expected)
                            finally:
                                os.close(fresh_fd)
                        finally:
                            os.close(fresh_dir)
                    for fd in ((stage_fd, final_fd) if linked else (stage_fd,)):
                        info = os.fstat(fd)
                        require(info.st_nlink == (2 if linked else 1)
                                and info.st_uid == os.geteuid()
                                and stat.S_IMODE(info.st_mode) == 0o600)
                    require(db.execute("SELECT 1 FROM placements WHERE path=? LIMIT 1",
                                       (str(stage),)).fetchone() is None)

                if create_link:
                    # Receipt already committed by admission; no fresh
                    # reservation, adoption or replacement of a destination.
                    from .derived_variant_compact_receipt import _measure_compact_in_transaction
                    from .derived_variant_retention_review import (
                        _index_receipt_references, _other_receipt_dependency,
                    )
                    _measure_compact_in_transaction(db, require_paired=True)
                    paths, inodes = {}, {}
                    for receipt_key, _size in journal._measure(db)[0]:
                        raw = journal._read_logical_receipt(db, receipt_key)
                        prior = journal._decode(receipt_key, raw)
                        _index_receipt_references(paths, inodes, receipt_key, prior)
                    require(not _other_receipt_dependency(paths, inodes, reservation.key, intent))

                    def absent():
                        _require_absent(parent_dir, final.name)
                        require(db.execute("SELECT 1 FROM placements WHERE path=? LIMIT 1",
                                           (str(final),)).fetchone() is None)

                    names(linked=False)
                    absent()
                    require(_hash(stage_fd, output[2]) == intent.output_sha256)
                    for fd in (stage_fd, stage_dir, parent_dir):
                        os.fsync(fd)
                    check_region()
                    names(linked=False)
                    absent()
                    # O_EXCL semantics of link: no overwrite. Any failure
                    # after link leaves both names and the durable receipt.
                    os.link(stage.name, final.name, src_dir_fd=stage_dir,
                            dst_dir_fd=parent_dir, follow_symlinks=False)
                    output = _regular(stage_fd)
                    require(output[:4] == intent.output_stamp
                            and _hash(stage_fd, output[2]) == intent.output_sha256)
                    os.fsync(stage_fd)
                    os.fsync(parent_dir)
                final_dir, final_fd = opened(final)
                names(linked=True)
                check_region()
                names(linked=True)
            return True
        except journal.DerivedJournalConflict:
            raise
        except (OSError, ValueError, TypeError, RuntimeError, KeyError):
            raise journal.DerivedJournalConflict("derived_private_recovery_unverified") from None


def recover_private_linked_derived_publication(
    storage, *, library_root, identity_id, reservation, approved_receipt_sha256,
    approved_binding_sha256, shared_lock, scope_current, explicitly_requested=False,
):
    """Explicit isolated recovery of a v3 *already linked* receipt.

    No transformation or hardlink creation. Only this entry point builds the
    guard from an exact receipt and region approval; legacy recovery refuses v3.
    The original stage and canonical file both survive every outcome.
    """
    if (explicitly_requested is not True
            or type(reservation) is not journal.DerivedReservation
            or reservation.receipt_sha256 != approved_receipt_sha256
            or not journal._digest(approved_receipt_sha256)
            or not journal._digest(approved_binding_sha256)
            or not isinstance(library_root, Path) or not library_root.is_absolute()
            or library_root == Path("/") or ".." in library_root.parts
            or not journal._text(identity_id) or not callable(scope_current)
            or not _lock_current(shared_lock)):
        raise journal.DerivedJournalConflict("derived_private_recovery_approval_required")
    guard = _PrivateRecoveryGuard(storage, library_root, identity_id, reservation,
                                  approved_binding_sha256, shared_lock, scope_current)
    return recover_linked_derived_publication(
        storage, library_root=library_root, identity_id=identity_id,
        reservation=reservation, approved_receipt_sha256=approved_receipt_sha256,
        shared_lock=shared_lock, scope_current=scope_current, _private_guard=guard)


def link_private_reserved_variant(
    storage, *, library_root, identity_id, reservation, approved_receipt_sha256,
    approved_binding_sha256, shared_lock, scope_current, explicitly_requested=False,
):
    """Link one admitted private stage; never commit B or adopt an old target.

    A crash after the exclusive link leaves a pending receipt for the
    separately confirmed linked recovery. No automatic fallback or unlink.
    """
    if (explicitly_requested is not True
            or type(reservation) is not journal.DerivedReservation
            or reservation.receipt_sha256 != approved_receipt_sha256
            or not journal._digest(approved_receipt_sha256)
            or not journal._digest(approved_binding_sha256)
            or not isinstance(library_root, Path) or not library_root.is_absolute()
            or library_root == Path("/") or ".." in library_root.parts
            or not journal._text(identity_id) or not callable(scope_current)
            or not _lock_current(shared_lock)):
        raise journal.DerivedJournalConflict("derived_private_recovery_approval_required")
    guard = _PrivateRecoveryGuard(storage, library_root, identity_id, reservation,
                                  approved_binding_sha256, shared_lock, scope_current)
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise journal.DerivedJournalConflict("derived_journal_nested_transaction")
        with db:
            db.execute("BEGIN IMMEDIATE")
            raw, intent, record = journal._read_pending_receipt(db, reservation)
            if guard.link(db, reservation, intent, record) is not True:
                raise journal.DerivedJournalConflict("derived_private_recovery_unverified")
            if journal._read_pending_receipt(db, reservation) != (raw, intent, record):
                raise journal.DerivedJournalConflict("derived_journal_reservation_changed")
    return {"status": "linked_pending", "destructive_cleanup_allowed": False}
