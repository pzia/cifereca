"""#6937 private stage retirement protocol, deliberately unarmed in production.

The only physical mutation is unlink of the exact v3 temporary name. An
exclusive consumer fence is mandatory and currently unavailable: no live
Core caller, plugin or UI may perform this transition. Isolated tests replace
the fence at its single internal boundary, using only synthetic FS/SQLite.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
from secrets import token_hex
import sqlite3
import stat
from time import monotonic
import zipfile

from xml.etree.ElementTree import ParseError
from . import derived_variant_journal as journal
from . import derived_variant_private_region as region
from .derived_variant_compact_receipt import _measure_compact_in_transaction
from .derived_variant_compact_shadow import _read_committed, _journal_current
from .derived_variant_link import _regular, _require_named
from .derived_variant_retention_review import _canonical
from .derived_variant_retirement_preparation import _inspect_epub_final
from .derived_variant_pdf_reader import pdf_reader_observed
from .derived_variant_stage_dependencies import scan_stage_references
from .derived_variant_stage_symlinks import _walk, _receipt_alias_verdict
from .derived_variant_supervised_recovery import _exact_scope, _lock_current
from .library_inventory import _open_root

_PREFIX = "derived.publication.retirement.v1:"
_END = _PREFIX[:-1] + ";"
_SCHEMA = "derived.retirement.v1"
# One fixed-size trace per maximum original journal record. Separate,
# conservative budget; neither trace nor unlink releases journal quota.
_MAX_RECORDS = journal._MAX_RECORDS
_MAX_RECORD_BYTES = 2048
_MAX_TOTAL_BYTES = _MAX_RECORDS * _MAX_RECORD_BYTES
_MAX_CONTENT = 64 * 1024 * 1024
_MAX_EXPANDED = 256 * 1024 * 1024
_MAX_PARENT = 1024 ** 3
_SECONDS = 3


def _require(ok, code="derived_retirement_unverified"):
    if not ok:
        raise journal.DerivedJournalConflict(code)


def _exclusive_consumer_fence_current(storage, root, identity_id, shared_lock):
    """No production proof yet; POSIX ownership and nlink are insufficient.

    Replacing this refusal requires the separately reviewed consumer
    exclusivity contract. Never expose a callback/boolean override to callers.
    """
    return False


def _stamp(value, length):
    return (type(value) is list and len(value) == length
            and all(type(n) is int and 0 <= n <= 2**63 - 1 for n in value))


def _decode_trace(key, raw):
    # SQLite metadata can be a corrupted TEXT value containing Unicode.
    # Reject it as non-canonical evidence rather than leaking UnicodeEncodeError.
    _require(type(raw) is str and raw.isascii()
             and 0 < len(raw) <= _MAX_RECORD_BYTES)
    value = json.loads(raw, object_pairs_hook=journal._unique_pairs)
    _require(type(value) is dict and set(value) == {
        "schema", "receipt", "binding", "nonce", "state", "before", "after",
        "region", "destination", "mount", "outcome",
    } and journal._encode(value) == raw and value["schema"] == _SCHEMA
        and journal._digest(value["receipt"]) and key == _PREFIX + value["receipt"]
        and journal._digest(value["binding"])
        and type(value["nonce"]) is str and len(value["nonce"]) == 32
        and all(ch in "0123456789abcdef" for ch in value["nonce"])
        and _stamp(value["before"], 5) and value["before"][2] > 0
        and _stamp(value["region"], 2) and _stamp(value["destination"], 2)
        and type(value["mount"]) is int and value["mount"] >= 0)
    if value["state"] == "prepared":
        _require(value["after"] is None and value["outcome"] is None)
    else:
        _require(value["state"] == "retired" and _stamp(value["after"], 5)
                 and value["after"][:4] == value["before"][:4]
                 and value["outcome"] in ("unlink_confirmed", "absence_reconciled"))
    return value


def _trace_snapshot(db):
    rows = db.execute(
        "SELECT key,length(CAST(value AS BLOB)) FROM metadata "
        "WHERE key>=? AND key<? ORDER BY key LIMIT ?",
        (_PREFIX, _END, _MAX_RECORDS + 1),
    ).fetchall()
    _require(len(rows) <= _MAX_RECORDS, "derived_retirement_capacity")
    result = {}
    total = 0
    for key, size in rows:
        _require(type(size) is int and 0 < size <= _MAX_RECORD_BYTES)
        raw = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0]
        trace = _decode_trace(key, raw)
        # Reserve the full fixed per-record budget for a prepared trace.
        # After unlink its terminal witness grows (after stamp + outcome).
        # Refuse before the physical transition if that space is not held.
        total += _MAX_RECORD_BYTES if trace["state"] == "prepared" else size
        _require(total <= _MAX_TOTAL_BYTES, "derived_retirement_capacity")
        result[key] = (raw, trace)
    return result, total


@contextmanager
def _transaction(storage):
    with storage._lock:
        db = storage._connect()
        _require(not db.in_transaction, "derived_retirement_nested_transaction")
        previous = db.execute("PRAGMA synchronous").fetchone()[0]
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.execute(f"PRAGMA synchronous={int(previous)}")


def _read_content(fd, intent, *, deadline, still_current):
    """Stream exact SHA, then the same bounded spine/CRC proof as preflight."""
    def live():
        return still_current() is True and monotonic() < deadline
    _require(intent.media_format in ("epub", "kepub", "pdf")
             and 0 < intent.output_size <= _MAX_CONTENT and live(),
             "derived_retirement_reader_unverified")
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = intent.output_size
    while remaining:
        _require(live(), "derived_retirement_reader_unverified")
        block = os.read(fd, min(remaining, 1024 * 1024))
        _require(bool(block), "derived_retirement_reader_unverified")
        remaining -= len(block)
        digest.update(block)
    _require(live() and not os.read(fd, 1)
             and digest.hexdigest() == intent.output_sha256,
             "derived_retirement_reader_unverified")
    if intent.media_format == "pdf":
        _require(pdf_reader_observed(fd, intent.output_size, deadline=deadline,
                                     still_current=live),
                 "derived_retirement_reader_unverified")
    else:
        _inspect_epub_final(
            fd, intent.output_size, media_format=intent.media_format,
            max_bytes=_MAX_EXPANDED, deadline=deadline, still_current=live)


@contextmanager
def _evidence(storage, db, *, root, identity_id, receipt, binding, lock, current):
    def live():
        _require(_lock_current(lock) and current(db) is True
                 and _exclusive_consumer_fence_current(storage, root, identity_id, lock) is True,
                 "derived_retirement_consumer_fence_unavailable")

    live()
    key = journal._COMMITTED + receipt
    raw, intent = _read_committed(db, key)
    record = json.loads(raw, object_pairs_hook=journal._unique_pairs)
    _require(record["schema"] == journal._PRIVATE_SCHEMA
             and record["region_binding_sha256"] == binding
             and intent.identity_id == identity_id
             and intent.parent_stamp[2] <= _MAX_PARENT)
    parent, stage, target = map(Path, (intent.parent_path, intent.staged_path, intent.candidate_path))
    profile = root / region._directory(db, identity_id)
    _require(stage.parent == profile / region._REGION)
    binding_key = region._key(identity_id)
    binding_raw = region._read_record(db, binding_key)
    _require(binding_raw is not None and journal._receipt_digest(binding_raw) == binding)

    def sql_and_region():
        live()
        _require(_read_committed(db, key) == (raw, intent)
                 and region._read_record(db, binding_key) == binding_raw
                 and _exact_scope(db, identity_id=identity_id, root=root, intent=intent))
        _canonical(storage, db, intent)
        _journal_current(db, key, intent)
        _measure_compact_in_transaction(db, require_paired=True)
        _require(scan_stage_references(db, {intent.staged_path: key}) ==
                 {intent.staged_path: "observed_clear"}, "derived_retirement_sql_dependency")
        result = region._ensure_region(
            db, storage=storage, root=root, identity_id=identity_id,
            placement_id=intent.parent_placement_id, shared_lock=lock,
            scope_current=current, provision=False,
        )
        _require(result["status"] == "verified")
        live()

    sql_and_region()
    with ExitStack() as stack:
        def directory(path):
            fd = _open_root(path)
            stack.callback(os.close, fd)
            return fd

        stage_dir, final_dir, parent_dir = map(directory, (stage.parent, target.parent, parent.parent))
        mount = region._mount_id(stage_dir)
        region_stamp = region._private_directory(stage_dir, exact=True)
        final_stamp = region._directory_stamp(final_dir)
        _require(region._directory_stamp(parent_dir) == final_stamp
                 and region._mount_id(parent_dir) == region._mount_id(final_dir) == mount)
        def opened(name, folder):
            fd = os.open(name, region._FLAGS, dir_fd=folder)
            stack.callback(os.close, fd)
            _require(region._mount_id(fd) == mount)
            return fd

        parent_fd = opened(parent.name, parent_dir)
        final_fd = opened(target.name, final_dir)

        def check_names():
            live()
            original = _regular(parent_fd)
            output = _regular(final_fd)
            _require(original == intent.parent_stamp and output[:4] == intent.output_stamp
                     and output[:2] != original[:2])
            _require_named(parent, parent_dir, parent_fd, original)
            _require_named(target, final_dir, final_fd, output)
            # Reopen names through fresh no-follow directories to reject
            # same-device mount substitutions as well as inode changes.
            for path, wanted, folder in ((parent, original, parent_dir), (target, output, final_dir)):
                fresh = _open_root(path.parent)
                try:
                    _require(region._directory_stamp(fresh) == region._directory_stamp(folder)
                             and region._mount_id(fresh) == mount)
                    fd = os.open(path.name, region._FLAGS, dir_fd=fresh)
                    try:
                        _require(region._mount_id(fd) == mount and _regular(fd) == wanted)
                    finally:
                        os.close(fd)
                finally:
                    os.close(fresh)
            fresh = _open_root(stage.parent)
            try:
                _require(region._private_directory(fresh, exact=True) == region_stamp
                         and region._mount_id(fresh) == mount)
                try:
                    stage_fd = os.open(stage.name, region._FLAGS, dir_fd=fresh)
                except FileNotFoundError:
                    linked = False
                else:
                    try:
                        info = os.fstat(stage_fd)
                        _require(_regular(stage_fd) == output and region._mount_id(stage_fd) == mount
                                 and info.st_nlink == 2)
                        _require_named(stage, stage_dir, stage_fd, output)
                    finally:
                        os.close(stage_fd)
                    linked = True
                actual = os.fstat(final_fd)
                _require(actual.st_nlink == (2 if linked else 1)
                         and actual.st_uid == os.geteuid() and stat.S_IMODE(actual.st_mode) == 0o600
                         and _regular(final_fd) == output)
            finally:
                os.close(fresh)
            return linked, output

        def verify():
            deadline = monotonic() + _SECONDS
            walk = _walk(root, (intent.staged_path,), deadline=deadline)
            _require(_receipt_alias_verdict(walk, intent.staged_path) == "no_local_symlink_observed",
                     "derived_retirement_symlink_dependency")
            first = check_names()
            def reader_current():
                live()
                return True
            _read_content(final_fd, intent, deadline=deadline, still_current=reader_current)
            sql_and_region()
            _require(check_names() == first and _walk(root, (intent.staged_path,), deadline=deadline) == walk
                     and monotonic() < deadline, "derived_retirement_evidence_changed")
            return first

        def flush():
            for fd in (final_fd, stage_dir, final_dir):
                os.fsync(fd)

        yield intent, stage, stage_dir, verify, check_names, flush, region_stamp, final_stamp, mount


def _common(storage, root, identity_id, receipt, binding, shared_lock, scope_current,
            explicitly_requested):
    _require(explicitly_requested is True and isinstance(root, Path) and root.is_absolute()
             and root != Path("/") and ".." not in root.parts and journal._text(identity_id)
             and journal._digest(receipt) and journal._digest(binding)
             and callable(scope_current) and _lock_current(shared_lock),
             "derived_retirement_approval_required")
    _require(_exclusive_consumer_fence_current(storage, root, identity_id, shared_lock) is True,
             "derived_retirement_consumer_fence_unavailable")


def _result(status, **extra):
    return {"status": status, "quota_refund_authorized": False,
            "destructive_cleanup_allowed": False, **extra}


def prepare_private_stage_retirement(storage, *, library_root, identity_id,
                                     receipt_sha256, approved_binding_sha256,
                                     shared_lock, scope_current, explicitly_requested=False):
    """Persist one exact pending retirement, BEFORE any unlink. No live fence."""
    _common(storage, library_root, identity_id, receipt_sha256, approved_binding_sha256,
            shared_lock, scope_current, explicitly_requested)
    key = _PREFIX + receipt_sha256
    try:
        with _transaction(storage) as db:
            records, total = _trace_snapshot(db)
            _require(key not in records and len(records) < _MAX_RECORDS,
                     "derived_retirement_slot_occupied")
            with _evidence(storage, db, root=library_root, identity_id=identity_id,
                           receipt=receipt_sha256, binding=approved_binding_sha256,
                           lock=shared_lock, current=scope_current) as proof:
                _intent, _stage, _fd, verify, names, flush, rs, ds, mount = proof
                linked, before = verify()
                _require(linked, "derived_retirement_stage_absent")
                flush()
                _require(verify() == (linked, before))
                raw = journal._encode({
                    "schema": _SCHEMA, "receipt": receipt_sha256, "binding": approved_binding_sha256,
                    "nonce": token_hex(16), "state": "prepared", "before": list(before),
                    "after": None, "region": list(rs), "destination": list(ds),
                    "mount": mount, "outcome": None,
                })
                _decode_trace(key, raw)
                _require(total + _MAX_RECORD_BYTES <= _MAX_TOTAL_BYTES,
                         "derived_retirement_capacity")
                db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (key, raw))
                _require(names() == (linked, before)
                         and _trace_snapshot(db)[0].get(key, (None,))[0] == raw)
        return _result("prepared", intent_sha256=journal._receipt_digest(raw))
    except journal.DerivedJournalConflict:
        raise
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error,
            zipfile.BadZipFile, NotImplementedError, UnicodeError, RecursionError, ParseError):
        raise journal.DerivedJournalConflict("derived_retirement_unverified") from None


def retire_private_stage(storage, *, library_root, identity_id, receipt_sha256,
                         approved_binding_sha256, approved_intent_sha256, shared_lock,
                         scope_current, explicitly_requested=False):
    """Execute exact prepared intent or reconcile absence after a crash.

    A crash after unlink leaves a durable prepared row. Never unlink a
    replacement, final name, original, or any other stage; no auto-retry.
    Recovery accepts absence only with the same final inode/bytes and fence,
    and records observation rather than claiming who removed the name.
    """
    _common(storage, library_root, identity_id, receipt_sha256, approved_binding_sha256,
            shared_lock, scope_current, explicitly_requested)
    _require(journal._digest(approved_intent_sha256), "derived_retirement_approval_required")
    key = _PREFIX + receipt_sha256
    try:
        with _transaction(storage) as db:
            records, _total = _trace_snapshot(db)
            prior = records.get(key)
            _require(prior is not None and journal._receipt_digest(prior[0]) == approved_intent_sha256,
                     "derived_retirement_intent_changed")
            raw, trace = prior
            _require(trace["state"] == "prepared" and trace["binding"] == approved_binding_sha256)
            with _evidence(storage, db, root=library_root, identity_id=identity_id,
                           receipt=receipt_sha256, binding=approved_binding_sha256,
                           lock=shared_lock, current=scope_current) as proof:
                _intent, stage, stage_dir, verify, names, flush, rs, ds, mount = proof
                linked, output = verify()
                _require(trace["region"] == list(rs) and trace["destination"] == list(ds)
                         and trace["mount"] == mount
                         and trace["before"][:4] == list(output[:4]))
                if linked:
                    _require(trace["before"] == list(output) and names() == (True, output))
                    _require(db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone() == (raw,))
                    # The fence, exact no-follow name, nlink and prepared CAS
                    # are all required immediately before this ONLY unlink.
                    _require(names() == (True, output))
                    os.unlink(stage.name, dir_fd=stage_dir)
                flush()
                remaining, final = verify()
                _require(not remaining and final[:4] == output[:4])
                trace = dict(trace, state="retired", after=list(final),
                             outcome="unlink_confirmed" if linked else "absence_reconciled")
                changed = journal._encode(trace)
                _decode_trace(key, changed)
                _require(db.execute("UPDATE metadata SET value=? WHERE key=? AND value=?",
                                    (changed, key, raw)).rowcount == 1,
                         "derived_retirement_intent_changed")
                _require(names() == (False, final) and _trace_snapshot(db)[0].get(key, (None,))[0] == changed)
        return _result("retired", outcome=trace["outcome"])
    except journal.DerivedJournalConflict:
        raise
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error,
            zipfile.BadZipFile, NotImplementedError, UnicodeError, RecursionError, ParseError):
        raise journal.DerivedJournalConflict("derived_retirement_unverified") from None
