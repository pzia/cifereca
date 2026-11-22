"""#6937: supervised durable shadow and exact logical compaction.

Unwired private Core foundation. Does not grant a replay, prove consumers
absent, establish reader usability, release quota or remove physical names.
"""
from pathlib import Path
import sqlite3

from . import derived_variant_compact_receipt as compact
from .derived_variant_journal import (
    DerivedJournalConflict, _COMMITTED, _decode, _digest, _measure, _receipt_digest,
    _read_logical_receipt,
)
from .derived_variant_publication import DerivedPublicationConflict, _verified_variant_file
from .derived_variant_retention_physical import _pair
from .derived_variant_retention_review import (
    _canonical, _index_receipt_references, _other_receipt_dependency,
)
from .derived_variant_supervised_recovery import _exact_scope, _lock_current

_MAX_BYTES = 1024 ** 3


def _require(ok, code="derived_shadow_unverified"):
    if not ok:
        raise DerivedJournalConflict(code)


def _read_committed(db, key):
    raw = _read_logical_receipt(db, key)
    _require(type(raw) is str and 0 < len(raw.encode("utf-8")) <= compact._MAX_PROOF_BYTES,
             "derived_shadow_receipt_changed")
    _require(key == _COMMITTED + _receipt_digest(raw), "derived_shadow_receipt_changed")
    return raw, _decode(key, raw)


def _journal_current(db, key, intent):
    # Use the same recorded-coordinate veto as the retention read-model.
    # Neither view infers absence of external FS consumers; only an
    # independently admitted physical reviewer may supply that evidence.
    rows, _ = _measure(db)
    paths, inodes = {}, {}
    for other, _size in rows:
        raw = _read_logical_receipt(db, other)
        prior = _decode(other, raw)
        _index_receipt_references(paths, inodes, other, prior)
    _require(not _other_receipt_dependency(paths, inodes, key, intent),
             "derived_shadow_ambiguous_receipt")
    _require(db.execute("SELECT 1 FROM placements WHERE path=? LIMIT 1",
                        (intent.staged_path,)).fetchone() is None,
             "derived_shadow_ambiguous_receipt")


def write_compact_shadow(storage, *, library_root, identity_id, receipt_sha256,
                         approved_receipt_sha256, shared_lock, scope_current,
                         explicitly_requested=False):
    """Insert one exact reversible shadow in a FULL SQLite transaction."""
    return _write_or_retire(storage, library_root=library_root, identity_id=identity_id,
                            receipt_sha256=receipt_sha256,
                            approved_receipt_sha256=approved_receipt_sha256,
                            shared_lock=shared_lock, scope_current=scope_current,
                            explicitly_requested=explicitly_requested)


def retire_compact_original(storage, *, library_root, identity_id, receipt_sha256,
                            approved_receipt_sha256, approved_compact_sha256,
                            shared_lock, scope_current, explicitly_requested=False):
    """Isolated logical compaction: retire only the byte-exact duplicate row.

    No FS mutation, quota refund, automatic call or production activation.
    A prior durable shadow and exact separate confirmation are mandatory.
    """
    _require(_digest(approved_compact_sha256), "derived_compact_approval_required")
    return _write_or_retire(storage, library_root=library_root, identity_id=identity_id,
                            receipt_sha256=receipt_sha256,
                            approved_receipt_sha256=approved_receipt_sha256,
                            shared_lock=shared_lock, scope_current=scope_current,
                            explicitly_requested=explicitly_requested,
                            approved_compact_sha256=approved_compact_sha256)


def _write_or_retire(storage, *, library_root, identity_id, receipt_sha256,
                     approved_receipt_sha256, shared_lock, scope_current,
                     explicitly_requested, approved_compact_sha256=None):
    _require(explicitly_requested is True and _digest(receipt_sha256)
             and approved_receipt_sha256 == receipt_sha256
             and isinstance(library_root, Path) and library_root.is_absolute()
             and library_root != Path("/") and ".." not in library_root.parts
             and type(identity_id) is str and 0 < len(identity_id) <= 1024
             and callable(scope_current) and _lock_current(shared_lock),
             "derived_shadow_approval_required")
    key = _COMMITTED + receipt_sha256
    try:
        with storage._lock:
            db = storage._connect()
            _require(not db.in_transaction, "derived_shadow_nested_transaction")
            sync = db.execute("PRAGMA synchronous").fetchone()[0]
            try:
                db.execute("PRAGMA synchronous=FULL")
                with db:
                    db.execute("BEGIN IMMEDIATE")

                    def live():
                        _require(_lock_current(shared_lock) and scope_current(db) is True,
                                 "derived_shadow_scope_changed")

                    live()
                    raw, intent = _read_committed(db, key)
                    _require(compact._retired_receipt(db, key) is None,
                             "derived_compact_already_retired")
                    before_charge = _measure(db)
                    marker = compact._RETIRED_PREFIX + receipt_sha256

                    def canonical():
                        _require(_exact_scope(db, identity_id=identity_id,
                                              root=library_root, intent=intent),
                                 "derived_shadow_scope_changed")
                        _require(_read_committed(db, key) == (raw, intent),
                                 "derived_shadow_receipt_changed")
                        _canonical(storage, db, intent)
                        _journal_current(db, key, intent)

                    canonical()
                    _require(intent.parent_stamp[2] + intent.output_size <= _MAX_BYTES,
                             "derived_shadow_budget_exceeded")
                    proof_key, proof = compact.encode_committed_witness(key, raw)
                    count, total = compact._measure_compact_in_transaction(db, require_paired=True)
                    existing = db.execute("SELECT value FROM metadata WHERE key=?",
                                          (proof_key,)).fetchone()
                    if existing is not None:
                        _require(existing == (proof,), "derived_shadow_receipt_changed")
                    else:
                        _require(count < compact._MAX_PROOFS
                                 and total + len(proof.encode("ascii")) <= compact._MAX_PROOF_TOTAL,
                                 "derived_compact_capacity_exceeded")
                    if approved_compact_sha256 is not None:
                        _require(existing == (proof,) and _receipt_digest(proof) == approved_compact_sha256
                                 and compact._raw_size(proof) + 64 < compact._raw_size(raw),
                                 "derived_compact_approval_required")
                    parent, stage, target = map(Path, (
                        intent.parent_path, intent.staged_path, intent.candidate_path,
                    ))
                    with (
                        _verified_variant_file(parent, intent.parent_sha256,
                                               expected_size=intent.parent_stamp[2]) as parent_current,
                        _verified_variant_file(target, intent.output_sha256,
                                               expected_size=intent.output_size) as target_current,
                    ):
                        original, output = parent_current(), target_current()
                        _require((original[0], original[1], *original[3:]) == intent.parent_stamp
                                 and (output[0], output[1], *output[3:5]) == intent.output_stamp)
                        pair = _pair(stage, target, output)
                        _require(pair[0] == "linked_pair_observed",
                                 "derived_shadow_ambiguous_link")

                        def recheck():
                            live()
                            canonical()
                            _require(parent_current() == original
                                     and target_current() == output
                                     and _pair(stage, target, output) == pair)

                        recheck()
                        if approved_compact_sha256 is None:
                            if existing is None:
                                db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                                           (proof_key, proof))
                        else:
                            # A reader cannot observe an intermediate state:
                            # both writes and the revalidation are in one FULL
                            # transaction, with an exact CAS on the old bytes.
                            _require(db.execute("SELECT value FROM metadata WHERE key=?",
                                                (proof_key,)).fetchone() == (proof,))
                            db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                                       (marker, approved_compact_sha256))
                            _require(db.execute("DELETE FROM metadata WHERE key=? AND value=?",
                                                (key, raw)).rowcount == 1,
                                     "derived_shadow_receipt_changed")
                            _require(compact._retired_receipt(db, key) == raw
                                     and _measure(db) == before_charge)
                        _require(db.execute("SELECT value FROM metadata WHERE key=?",
                                            (proof_key,)).fetchone() == (proof,))
                        recheck()
                        compact._measure_compact_in_transaction(db, require_paired=True)
                # FULL commit is completed here before returning any result.
            finally:
                db.execute(f"PRAGMA synchronous={int(sync)}")
        if approved_compact_sha256 is not None:
            return {"status": "logical_original_retired", "original_retained": False,
                    "receipt_reconstructible": True, "retention_authorized": False,
                    "quota_refund_authorized": False, "destructive_cleanup_allowed": False}
        return {"status": "shadow_written" if existing is None else "shadow_already_present",
                "original_retained": True, "retention_authorized": False,
                "quota_refund_authorized": False, "destructive_cleanup_allowed": False}
    except DerivedJournalConflict:
        raise
    except (DerivedPublicationConflict, OSError, sqlite3.Error, ValueError,
            TypeError, RuntimeError, RecursionError):
        raise DerivedJournalConflict("derived_shadow_unverified") from None
