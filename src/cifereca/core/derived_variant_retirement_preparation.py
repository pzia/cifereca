"""#6937: durable local preflight, NOT an authority for unlink or refund.

Unwired Core-only review. A registered private region and a locally clear
dependency scan cannot establish consumer exclusivity outside Core.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sqlite3
import stat
from time import monotonic
from xml.etree import ElementTree
import zipfile
import zlib

from ..artifact_inspection import _epub_encryption_protected, _validate_epub_archive
from ..epub_metadata import MAX_XML, _bounded_directory, _member, _read, _xml
from . import derived_variant_journal as journal
from . import derived_variant_compact_receipt as compact
from . import derived_variant_private_region as region
from .derived_variant_compact_shadow import _read_committed, _journal_current
from .derived_variant_retention_review import _canonical
from .derived_variant_stage_dependencies import scan_stage_references
from .derived_variant_stage_symlinks import _receipt_alias_verdict, _walk
from .derived_variant_supervised_recovery import _exact_scope, _lock_current
from .derived_variant_link import _hash, _regular, _require_named
from .library_inventory import _open_root

_PREFIX = "derived.retention.prepared.v1:"
_END = _PREFIX[:-1] + ";"
_SCHEMA = "derived.retention.prepared.v1"
# A single fixed-size checkpoint per still-retained receipt. No user strings,
# paths or unbounded history; no effect on either existing journal budget.
_MAX_RECORD_BYTES = 512
_MAX_BYTES = 1024 ** 3
_SECONDS = 3


def _require(ok, code="derived_retirement_preparation_unverified"):
    if not ok:
        raise journal.DerivedJournalConflict(code)


def _inspect_epub_final(fd, size):
    """Pinned, bounded EPUB container and spine readability, not rendering.

    PDFs are deliberately not accepted by this tranche: a PDF signature or
    EOF marker is not a reader proof and Core has no qualified PDF reader.
    """
    with os.fdopen(os.dup(fd), "rb") as stream:
        _bounded_directory(stream)
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            # Bound expansion before any full CRC pass. Reject symlink and
            # ambiguous archive entries; parse XML using the safe shared reader.
            _require(sum(i.file_size for i in infos) + size <= _MAX_BYTES
                     and all(i.file_size <= _MAX_BYTES
                             and i.file_size <= 1000 * max(i.compress_size, 1)
                             and not i.flag_bits & 1
                             and (i.external_attr >> 16) & 0o170000 != 0o120000
                             and not i.filename.startswith("/")
                             and ".." not in i.filename.split("/")
                             and chr(92) not in i.filename for i in infos),
                     "derived_retirement_reader_unverified")
            names = {i.filename for i in infos}
            _require(len(names) == len(infos)
                     and "META-INF/encryption.xml" not in names,
                     "derived_retirement_reader_unverified")
            container = _xml(_read(archive, "META-INF/container.xml", MAX_XML))
            roots = [n.get("full-path") for n in container.iter()
                     if n.tag.rsplit("}", 1)[-1] == "rootfile"]
            _require(len(roots) == 1 and bool(roots[0]), "derived_retirement_reader_unverified")
            package_name = _member("", roots[0])
            _require(package_name == roots[0], "derived_retirement_reader_unverified")
            package = _xml(_read(archive, package_name, MAX_XML))
            opf = "{http://www.idpf.org/2007/opf}"
            manifests, spines = package.findall(opf + "manifest"), package.findall(opf + "spine")
            _require(package.tag == opf + "package" and len(manifests) == len(spines) == 1,
                     "derived_retirement_reader_unverified")
            manifest = {}
            for item in manifests[0].findall(opf + "item"):
                key, href = item.get("id"), item.get("href")
                _require(bool(key) and bool(href) and key not in manifest,
                         "derived_retirement_reader_unverified")
                manifest[key] = item
            items = spines[0].findall(opf + "itemref")
            _require(bool(items), "derived_retirement_reader_unverified")
            for itemref in items:
                item = manifest.get(itemref.get("idref"))
                _require(item is not None and item.get("media-type") == "application/xhtml+xml",
                         "derived_retirement_reader_unverified")
                member = _member(str(Path(package_name).parent), item.get("href"))
                _require(_xml(_read(archive, member, MAX_XML)).tag
                         == "{http://www.w3.org/1999/xhtml}html",
                         "derived_retirement_reader_unverified")
            _require(_validate_epub_archive(archive) is None
                     and not _epub_encryption_protected(archive),
                     "derived_retirement_reader_unverified")


def _checkpoint(raw, key):
    _require(type(raw) is str and 0 < len(raw.encode("ascii")) <= _MAX_RECORD_BYTES)
    body = json.loads(raw, object_pairs_hook=journal._unique_pairs)
    _require(type(body) is dict and set(body) == {
        "schema", "receipt", "binding", "output_stamp", "status"
    } and body["schema"] == _SCHEMA
        and body["status"] == "blocked_external_consumers"
        and journal._digest(body["receipt"]) and key == _PREFIX + body["receipt"]
        and journal._digest(body["binding"])
        and type(body["output_stamp"]) is list
        and journal._stamp(tuple(body["output_stamp"]), 5)
        and journal._encode(body) == raw)
    return body


def _measure(db):
    rows = db.execute(
        "SELECT key,CASE WHEN length(CAST(value AS BLOB)) BETWEEN 1 AND ? "
        "THEN value ELSE NULL END FROM metadata WHERE key>=? AND key<? "
        "ORDER BY key LIMIT ?", (_MAX_RECORD_BYTES, _PREFIX, _END, journal._MAX_RECORDS + 1)
    ).fetchall()
    _require(len(rows) <= journal._MAX_RECORDS, "derived_retirement_checkpoint_capacity")
    for key, raw in rows:
        body = _checkpoint(raw, key)
        original, _ = _read_committed(db, journal._COMMITTED + body["receipt"])
        record = json.loads(original, object_pairs_hook=journal._unique_pairs)
        _require(record.get("schema") == journal._PRIVATE_SCHEMA
                 and record.get("region_binding_sha256") == body["binding"])
    return dict(rows)


def prepare_private_retirement_review(storage, *, library_root, identity_id,
                                      receipt_sha256, approved_receipt_sha256,
                                      approved_binding_sha256, shared_lock,
                                      scope_current, explicitly_requested=False):
    """Durably record a blocked review of one already committed v3 pair.

    Fresh exact receipt, region binding, canonical rows, paired compact
    witness, known SQL and local symlink consumers, final bytes and EPUB
    container/spine are rechecked. No unlink, no reusable destructive
    approval, no quota refund. Any changed checkpoint is retained, never
    overwritten. Only one FULL SQLite commit records the blocked result.
    """
    _require(explicitly_requested is True and journal._digest(receipt_sha256)
             and receipt_sha256 == approved_receipt_sha256
             and journal._digest(approved_binding_sha256)
             and isinstance(library_root, Path) and library_root.is_absolute()
             and library_root != Path("/") and ".." not in library_root.parts
             and journal._text(identity_id) and callable(scope_current)
             and _lock_current(shared_lock), "derived_retirement_approval_required")
    key, checkpoint_key = journal._COMMITTED + receipt_sha256, _PREFIX + receipt_sha256
    try:
        with storage._lock:
            db = storage._connect()
            _require(not db.in_transaction, "derived_retirement_nested_transaction")
            sync = db.execute("PRAGMA synchronous").fetchone()[0]
            try:
                db.execute("PRAGMA synchronous=FULL")
                with db, ExitStack() as stack:
                    db.execute("BEGIN IMMEDIATE")
                    raw, intent = _read_committed(db, key)
                    record = json.loads(raw, object_pairs_hook=journal._unique_pairs)
                    _require(record["schema"] == journal._PRIVATE_SCHEMA
                             and record["region_binding_sha256"] == approved_binding_sha256
                             and intent.identity_id == identity_id
                             and intent.parent_stamp[2] + intent.output_size <= _MAX_BYTES)
                    _require(intent.media_format in ("epub", "kepub"),
                             "derived_retirement_reader_unverified")
                    directory = region._directory(db, identity_id)
                    parent, stage, final = map(Path, (
                        intent.parent_path, intent.staged_path, intent.candidate_path))
                    _require(stage.parent == library_root / directory / region._REGION)
                    binding_key = region._key(identity_id)
                    binding = region._read_record(db, binding_key)
                    _require(binding is not None
                             and journal._receipt_digest(binding) == approved_binding_sha256)
                    owner = json.loads(binding, object_pairs_hook=journal._unique_pairs)
                    proof_key, proof = compact.encode_committed_witness(key, raw)

                    def current_sql():
                        _require(_lock_current(shared_lock) and scope_current(db) is True
                                 and _exact_scope(db, identity_id=identity_id,
                                                  root=library_root, intent=intent)
                                 and _read_committed(db, key) == (raw, intent)
                                 and region._read_record(db, binding_key) == binding)
                        _canonical(storage, db, intent)
                        _journal_current(db, key, intent)
                        compact._measure_compact_in_transaction(db, require_paired=True)
                        _require(db.execute("SELECT value FROM metadata WHERE key=?",
                                            (proof_key,)).fetchone() == (proof,),
                                 "derived_retirement_compact_required")
                        _require(scan_stage_references(db, {str(stage): key}).get(str(stage))
                                 == "observed_clear", "derived_retirement_dependency_unverified")
                        verdict = region._ensure_region(
                            db, storage=storage, root=library_root, identity_id=identity_id,
                            placement_id=intent.parent_placement_id, shared_lock=shared_lock,
                            scope_current=scope_current, provision=False)
                        _require(verdict["status"] == "verified")

                    current_sql()
                    def opened(path):
                        folder = _open_root(path.parent)
                        stack.callback(os.close, folder)
                        fd = os.open(path.name, region._FLAGS, dir_fd=folder)
                        stack.callback(os.close, fd)
                        return folder, fd

                    parent_dir, parent_fd = opened(parent)
                    stage_dir, stage_fd = opened(stage)
                    final_dir, final_fd = opened(final)
                    original, output = _regular(parent_fd), _regular(final_fd)
                    mount = region._mount_id(stage_dir)
                    def physical():
                        _require(original == intent.parent_stamp
                                 and output[:4] == intent.output_stamp
                                 and original[:2] != output[:2]
                                 and _regular(stage_fd) == output
                                 and list(region._private_directory(stage_dir, exact=True))
                                     == owner["region_stamp"])
                        for path, folder, fd, expected in (
                                (parent, parent_dir, parent_fd, original),
                                (stage, stage_dir, stage_fd, output),
                                (final, final_dir, final_fd, output)):
                            _require_named(path, folder, fd, expected)
                            fresh = _open_root(path.parent)
                            try:
                                named = os.open(path.name, region._FLAGS, dir_fd=fresh)
                                try:
                                    _require(region._mount_id(fresh) == region._mount_id(named)
                                             == region._mount_id(folder) == region._mount_id(fd) == mount
                                             and _regular(named) == expected)
                                finally:
                                    os.close(named)
                            finally:
                                os.close(fresh)
                        for fd in (stage_fd, final_fd):
                            info = os.fstat(fd)
                            _require(info.st_nlink == 2 and info.st_uid == os.geteuid()
                                     and stat.S_IMODE(info.st_mode) == 0o600)

                    physical()
                    deadline = monotonic() + _SECONDS
                    witness = _walk(library_root, (str(stage),), deadline=deadline)
                    _require(_receipt_alias_verdict(witness, str(stage)) == "no_local_symlink_observed",
                             "derived_retirement_dependency_unverified")
                    _require(_hash(parent_fd, original[2]) == intent.parent_sha256
                             and _hash(final_fd, output[2]) == intent.output_sha256)
                    _inspect_epub_final(final_fd, output[2])
                    physical()
                    current_sql()
                    _require(_walk(library_root, (str(stage),), deadline=deadline) == witness,
                             "derived_retirement_evidence_changed")
                    checkpoint = journal._encode({
                        "schema": _SCHEMA, "receipt": receipt_sha256,
                        "binding": approved_binding_sha256, "output_stamp": list(output),
                        "status": "blocked_external_consumers",
                    })
                    _checkpoint(checkpoint, checkpoint_key)
                    prior = _measure(db)
                    existing = prior.get(checkpoint_key)
                    if existing is None:
                        _require(len(prior) < journal._MAX_RECORDS, "derived_retirement_checkpoint_capacity")
                        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                                   (checkpoint_key, checkpoint))
                    else:
                        _require(existing == checkpoint, "derived_retirement_evidence_changed")
                    current_sql()
                    physical()
                    _require(_measure(db).get(checkpoint_key) == checkpoint
                             and _walk(library_root, (str(stage),), deadline=deadline) == witness,
                             "derived_retirement_evidence_changed")
            finally:
                db.execute(f"PRAGMA synchronous={int(sync)}")
        return {"status": "blocked_external_consumers", "checkpoint_durable": True,
                "epub_structure_observed": True, "consumer_exclusivity_verified": False,
                "retention_authorized": False, "quota_refund_authorized": False,
                "destructive_cleanup_allowed": False}
    except journal.DerivedJournalConflict:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, RuntimeError, KeyError,
            OverflowError, RecursionError, UnicodeError, ElementTree.ParseError,
            zipfile.BadZipFile, zlib.error, NotImplementedError):
        raise journal.DerivedJournalConflict("derived_retirement_preparation_unverified") from None
