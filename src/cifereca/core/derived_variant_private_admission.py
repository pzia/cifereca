"""Isolated #6937 v3 admission. No transform, link, publication or cleanup."""
from contextlib import ExitStack
from dataclasses import asdict
import json
import os
from pathlib import Path
from secrets import token_hex
import sqlite3
import stat

from . import derived_variant_journal as journal
from . import derived_variant_private_region as region
from .derived_variant_link import _hash, _require_named, _require_absent, _regular
from .derived_variant_supervised_recovery import _exact_scope, _lock_current
from .library_inventory import _open_root

_MAX_BYTES = 1024 ** 3


def _require(ok, code="derived_private_admission_unverified"):
    if not ok:
        raise journal.DerivedJournalConflict(code)


def reserve_private_derived_publication(storage, *, library_root, identity_id,
                                        intent, approved_binding_sha256, shared_lock,
                                        scope_current, explicitly_requested=False):
    """Reserve an exact already-staged file, never authorize publication."""
    journal._validate(intent, schema=journal._PRIVATE_SCHEMA)
    _require(explicitly_requested is True and isinstance(library_root, Path)
             and library_root.is_absolute() and library_root != Path("/")
             and ".." not in library_root.parts and type(identity_id) is str
             and identity_id == intent.identity_id and callable(scope_current)
             and journal._digest(approved_binding_sha256) and _lock_current(shared_lock),
             "derived_private_admission_approval_required")
    _require(intent.parent_stamp[2] + intent.output_size <= _MAX_BYTES,
             "derived_private_admission_budget")
    parent, stage, target = map(Path, (intent.parent_path, intent.staged_path, intent.candidate_path))
    try:
        with storage._lock:
            db = storage._connect()
            _require(not db.in_transaction, "derived_journal_nested_transaction")
            sync = db.execute("PRAGMA synchronous").fetchone()[0]
            try:
                db.execute("PRAGMA synchronous=FULL")
                with ExitStack() as stack, db:
                    db.execute("BEGIN IMMEDIATE")

                    def live():
                        _require(_lock_current(shared_lock) and scope_current(db) is True
                                 and _exact_scope(db, identity_id=identity_id,
                                                  root=library_root, intent=intent),
                                 "derived_private_admission_scope_changed")

                    live()
                    directory = region._directory(db, identity_id)
                    _require(stage.parent == library_root / directory / region._REGION)
                    key = region._key(identity_id)
                    binding = region._read_record(db, key)
                    _require(binding is not None and journal._receipt_digest(binding) == approved_binding_sha256,
                             "derived_private_admission_binding_changed")
                    record = json.loads(binding, object_pairs_hook=journal._unique_pairs)

                    def region_current():
                        live()
                        _require(region._read_record(db, key) == binding,
                                 "derived_private_admission_binding_changed")
                        verdict = region._ensure_region(
                            db, storage=storage, root=library_root, identity_id=identity_id,
                            placement_id=intent.parent_placement_id, shared_lock=shared_lock,
                            scope_current=scope_current, provision=False)
                        _require(verdict["status"] == "verified")

                    region_current()

                    def opened(path):
                        fd = _open_root(path)
                        stack.callback(os.close, fd)
                        return fd

                    def file_opened(path, fd):
                        handle = os.open(path.name, region._FLAGS, dir_fd=fd)
                        stack.callback(os.close, handle)
                        return handle

                    parent_dir, stage_dir = opened(parent.parent), opened(stage.parent)
                    parent_fd, stage_fd = file_opened(parent, parent_dir), file_opened(stage, stage_dir)
                    original, output = _regular(parent_fd), _regular(stage_fd)
                    _require(original == intent.parent_stamp and output[:4] == intent.output_stamp
                             and output[:2] != original[:2]
                             and _hash(parent_fd, original[2]) == intent.parent_sha256
                             and _hash(stage_fd, output[2]) == intent.output_sha256)
                    mount = region._mount_id(stage_dir)

                    def named():
                        live()
                        _require_named(parent, parent_dir, parent_fd, original)
                        _require_named(stage, stage_dir, stage_fd, output)
                        info = os.fstat(stage_fd)
                        _require(info.st_nlink == 1 and info.st_uid == os.geteuid()
                                 and stat.S_IMODE(info.st_mode) == 0o600
                                 and list(region._private_directory(stage_dir, exact=True)) == record["region_stamp"]
                                 and all(region._mount_id(fd) == mount for fd in (parent_dir, parent_fd, stage_fd)))
                        with ExitStack() as fresh:
                            for path, pinned in ((parent, parent_fd), (stage, stage_fd)):
                                fd = _open_root(path.parent)
                                fresh.callback(os.close, fd)
                                handle = os.open(path.name, region._FLAGS, dir_fd=fd)
                                fresh.callback(os.close, handle)
                                _require(region._mount_id(fd) == region._mount_id(handle)
                                         == region._mount_id(pinned) == mount
                                         and _regular(handle) == _regular(pinned))
                        _require_absent(parent_dir, target.name)
                        _require(db.execute("SELECT 1 FROM placements WHERE path=? LIMIT 1",
                                            (str(stage),)).fetchone() is None)

                    named()
                    os.fsync(stage_fd)
                    os.fsync(stage_dir)
                    region_current()
                    named()
                    raw = journal._encode({"schema": journal._PRIVATE_SCHEMA, "nonce": token_hex(16),
                                           "intent": asdict(intent),
                                           "region_binding_sha256": approved_binding_sha256})
                    receipt = journal._reserve_in_transaction(db, intent, raw, private=True)
                    region_current()
                    named()
                    _require(db.execute("SELECT value FROM metadata WHERE key=?", (receipt.key,)).fetchone()
                             == (raw,))
                return receipt
            finally:
                db.execute(f"PRAGMA synchronous={int(sync)}")
    except journal.DerivedJournalConflict:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, RuntimeError, KeyError):
        raise journal.DerivedJournalConflict("derived_private_admission_unverified") from None
