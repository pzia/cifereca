"""P1 #4520: one atomic SQLite publication of a locally derived variant.

The filesystem is published exclusively by artifact_runtime *before* this
transaction. A failed SQL step cannot assert a partial placement/recipe/
origin. Orphan file recovery after a crash remains a separate bounded step.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any

from .library_inventory import _open_root
from .derived_variant_journal import (
    DerivedJournalConflict, require_derived_reservation_in_transaction,
    commit_derived_reservation_in_transaction,
)


class DerivedPublicationConflict(ValueError):
    """Scoped refusal: do not reinterpret a local variant as a supplier right."""


def _file_signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _verified_variant_file(path: Path, digest: str, *, expected_size=None):
    """Pin a regular file, hash its bytes, recheck its named inode on demand.

    No-follow traversal covers every directory component. Holding the FD is
    not a lock against external writers, so the caller must recheck before
    committing. Neither this function nor its error path removes any file.
    """
    if (not path.is_absolute() or not path.name or ".." in path.parts
            or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)):
        raise DerivedPublicationConflict("derived_variant_invalid_coordinates")
    directory_fd = file_fd = None
    try:
        try:
            directory_fd = _open_root(path.parent)
            directory_info = os.fstat(directory_fd)
            file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW |
                              os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd)
            before = os.fstat(file_fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_size <= 0
                    or (expected_size is not None and before.st_size != expected_size)):
                raise DerivedPublicationConflict("derived_variant_physical_proof_changed")
            checksum = hashlib.sha256()
            remaining = before.st_size
            while remaining:
                block = os.read(file_fd, min(1024 * 1024, remaining))
                if not block:
                    break
                checksum.update(block)
                remaining -= len(block)
            if remaining or checksum.hexdigest() != digest:
                raise DerivedPublicationConflict("derived_variant_physical_proof_changed")
            signature = _file_signature(before)
        except (OSError, ValueError, AttributeError, RuntimeError) as exc:
            raise DerivedPublicationConflict("derived_variant_physical_proof_changed") from exc

        def recheck():
            current_fd = None
            try:
                current_fd = _open_root(path.parent)
                current_dir = os.fstat(current_fd)
                named = os.stat(path.name, dir_fd=current_fd, follow_symlinks=False)
                if ((current_dir.st_dev, current_dir.st_ino)
                        != (directory_info.st_dev, directory_info.st_ino)
                        or _file_signature(named) != signature
                        or _file_signature(os.fstat(file_fd)) != signature):
                    raise DerivedPublicationConflict("derived_variant_physical_proof_changed")
            except (OSError, ValueError, AttributeError, RuntimeError) as exc:
                raise DerivedPublicationConflict("derived_variant_physical_proof_changed") from exc
            finally:
                if current_fd is not None:
                    os.close(current_fd)
            return signature

        recheck()
        yield recheck
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


@contextmanager
def _publication_connection(storage, *, durable):
    db = storage._connect()
    if db.in_transaction:
        raise DerivedPublicationConflict("derived_journal_nested_transaction")
    if not durable:
        with db:
            yield db
        return
    synchronous = db.execute("PRAGMA synchronous").fetchone()[0]
    try:
        db.execute("PRAGMA synchronous=FULL")
        with db:
            yield db
    finally:
        db.execute(f"PRAGMA synchronous={int(synchronous)}")


def publish_derived_variant(
    storage: Any, *, parent_artifact_id: str, parent_sha256: str,
    parent_placement_id: str, parent_path: Path, identity_id: str,
    published_path: Path, output_sha256: str, output_size: int,
    media_format: str, mime_type: str | None, variant_kind: str,
    transformer_id: str, transformer_version: str,
    profile_id: str | None, profile_version: str,
    options_fingerprint: str, book_id: str | None,
    warnings: tuple[str, ...] | list[str], facts: dict[str, Any],
    scope_current=None, journal_reservation=None, _private_guard=None,
) -> str:
    """Commit Artifact B, Placement B, OriginLink(derived), recipe and notice.

    All inputs describe one already SHA-verified physical output from Core's
    pinned exclusive-publication routine. No supplier SourceReference, loan or
    entitlement is inserted. This is never callable by a plugin directly.
    """
    if (not all(type(x) is str and x for x in (
        parent_artifact_id, parent_sha256, parent_placement_id,
        identity_id, output_sha256, transformer_id,
    )) or type(output_size) is not int or output_size <= 0
            or parent_sha256 == output_sha256
            or parent_artifact_id != "sha256:" + parent_sha256
            or any(len(digest) != 64 or any(
                ch not in "0123456789abcdef" for ch in digest)
                for digest in (parent_sha256, output_sha256))
            or not parent_path.is_absolute() or not published_path.is_absolute()
            or published_path == parent_path):
        raise DerivedPublicationConflict("derived_variant_invalid_coordinates")
    if _private_guard is not None and journal_reservation is None:
        raise DerivedPublicationConflict("derived_journal_private_region_review_required")
    private_options = {"_private_guard": _private_guard} if _private_guard is not None else {}
    artifact_id = "sha256:" + output_sha256
    recipe_id = storage._stable_id(
        "variant", parent_sha256, transformer_id, str(profile_id or ""),
        str(options_fingerprint or ""),
    )
    placement_id = storage._stable_id("placement", str(published_path))
    origin_id = storage._stable_id(
        "origin", "derived", identity_id, parent_placement_id,
        placement_id, artifact_id, recipe_id,
    )
    now = int(time.time())
    source_id = str(book_id or parent_artifact_id)
    origin_evidence = "derived:" + recipe_id + ":" + placement_id
    variant_meta = {
        "book_id": book_id or "", "warnings": list(warnings),
        "facts": dict(facts),
    }
    origin_meta = {
        "source_placement_id": parent_placement_id,
        "transformer_plugin_id": transformer_id,
        "transformer_version": transformer_version,
        "profile_plugin_id": profile_id or "",
        "profile_version": profile_version,
        "options_fingerprint": options_fingerprint,
        "source_sha256": parent_sha256,
        "result_sha256": output_sha256,
    }
    def dumped(value):
        return storage._json_dump(value)

    # Rehash outside the SQLite writer lock; keep the pinned handles until
    # the transaction exits. Check namespace and stat proofs inside BEGIN
    # both before writing and after the last scope callback.
    with (
        _verified_variant_file(parent_path, parent_sha256) as parent_current,
        _verified_variant_file(published_path, output_sha256, expected_size=output_size) as output_current,
        storage._lock,
        _publication_connection(storage, durable=journal_reservation is not None) as db,
    ):
        db.execute("BEGIN IMMEDIATE")
        if scope_current is not None and not scope_current(db):
            raise DerivedPublicationConflict("artifact_transform_scope_changed")
        parent_signature = parent_current()
        output_signature = output_current()
        journal_intent = None
        if journal_reservation is not None:
            try:
                journal_intent = require_derived_reservation_in_transaction(
                    db, journal_reservation, expected={
                        "identity_id": identity_id,
                        "parent_placement_id": parent_placement_id,
                        "parent_sha256": parent_sha256,
                        "parent_path": str(parent_path),
                        "candidate_path": str(published_path),
                        "output_sha256": output_sha256,
                        "output_size": output_size,
                        "parent_stamp": (parent_signature[0], parent_signature[1],
                                         *parent_signature[3:]),
                        "output_stamp": (output_signature[0], output_signature[1],
                                         *output_signature[3:5]),
                        "transformer_id": transformer_id,
                        "transformer_version": transformer_version,
                        "profile_id": profile_id or "",
                        "profile_version": profile_version,
                        "options_fingerprint": options_fingerprint,
                        "variant_kind": variant_kind,
                        "media_format": media_format,
                    }, **private_options,
                )
            except DerivedJournalConflict as exc:
                raise DerivedPublicationConflict(str(exc)) from None
        parent = db.execute(
            "SELECT artifact_id,identity_id,path,state FROM placements "
            "WHERE placement_id=?", (parent_placement_id,),
        ).fetchone()
        if parent != (parent_artifact_id, identity_id, str(parent_path), "present"):
            raise DerivedPublicationConflict("derived_variant_source_rebound")
        original = db.execute(
            "SELECT sha256 FROM artifacts WHERE artifact_id=?",
            (parent_artifact_id,),
        ).fetchone()
        if original != (parent_sha256,):
            raise DerivedPublicationConflict("derived_variant_parent_checksum_changed")
        # The caller just published this inode. Check its exact namespace
        # is not assigned to another placement, including a missing tombstone.
        if db.execute(
            "SELECT 1 FROM placements WHERE path=? LIMIT 1",
            (str(published_path),),
        ).fetchone():
            raise DerivedPublicationConflict("derived_variant_path_claimed")
        registered = db.execute(
            "SELECT 1 FROM identities WHERE identity_id=?",
            (identity_id,),
        ).fetchone()
        if not registered:
            raise DerivedPublicationConflict("derived_variant_identity_missing")
        previous = db.execute(
            "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if previous is not None and previous != (output_sha256, output_size):
            raise DerivedPublicationConflict("derived_variant_artifact_collision")
        if previous is None:
            db.execute(
                "INSERT INTO artifacts(artifact_id,sha256,size,media_format,mime_type,"
                "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (artifact_id, output_sha256, output_size, media_format, mime_type,
                 dumped({"artifact_kind": "file", "variant_kind": variant_kind,
                         "source_artifact_id": parent_artifact_id,
                         "book_id": book_id or ""}), now, now),
            )

        # One global recipe ID can be reused for another identity only when
        # the *exact same bytes* and source are proven; never rebind its SHA.
        prior_variant = db.execute(
            "SELECT artifact_id,source_artifact_id,source_sha256,"
            "transformer_plugin_id,options_fingerprint FROM artifact_variants "
            "WHERE variant_id=?", (recipe_id,),
        ).fetchone()
        expected_variant = (
            artifact_id, parent_artifact_id, parent_sha256,
            transformer_id, str(options_fingerprint or ""),
        )
        if prior_variant is not None and prior_variant != expected_variant:
            raise DerivedPublicationConflict("derived_variant_recipe_collision")
        if prior_variant is None:
            db.execute(
                "INSERT INTO artifact_variants(variant_id,artifact_id,source_artifact_id,"
                "source_sha256,variant_kind,transformer_plugin_id,transformer_version,"
                "profile_plugin_id,profile_version,options_fingerprint,metadata_json,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (recipe_id, artifact_id, parent_artifact_id, parent_sha256,
                 variant_kind, transformer_id, transformer_version, profile_id,
                 profile_version, str(options_fingerprint or ""),
                 dumped(variant_meta), now, now),
            )

        # The child file is an INTERNAL derivative, not a supplier copy.
        # Keep its book association bibliographic, not as provider evidence.
        source_catalogue = db.execute(
            "SELECT title,author,item_id,metadata_json FROM catalog_entries "
            "WHERE placement_id=? ORDER BY catalog_entry_id LIMIT 1",
            (parent_placement_id,),
        ).fetchone()
        if source_catalogue and type(source_catalogue[2]) is str and source_catalogue[2]:
            source_id = source_catalogue[2]
        db.execute(
            "INSERT INTO placements(placement_id,artifact_id,path,identity_id,"
            "provider_id,source_item_id,state,last_seen_at,metadata_json,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (placement_id, artifact_id, str(published_path), identity_id,
             "cifereca", source_id, "present", now,
             dumped({"variant": True, "variant_kind": variant_kind,
                     "source_artifact_id": parent_artifact_id,
                     "source_placement_id": parent_placement_id}), now, now),
        )
        # A user may delete the local derivative but retain its immutable
        # historical OriginLink. Recreating the exact same bytes in that same
        # placement must not fail or rewrite the previous source evidence.
        old_origin = db.execute(
            "SELECT origin_kind,identity_id,artifact_id,source_reference_id,"
            "evidence_ref,parent_artifact_id,metadata_json FROM origin_links "
            "WHERE origin_link_id=?", (origin_id,),
        ).fetchone()
        expected_origin = (
            "derived", identity_id, artifact_id, None, origin_evidence,
            parent_artifact_id, dumped(origin_meta),
        )
        if old_origin is None:
            db.execute(
                "INSERT INTO origin_links(origin_link_id,origin_kind,identity_id,"
                "artifact_id,source_reference_id,evidence_ref,parent_artifact_id,"
                "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (origin_id, *expected_origin, now, now),
            )
        elif old_origin != expected_origin:
            raise DerivedPublicationConflict("derived_variant_origin_collision")
        if source_catalogue is not None:
            title, author, _source_item, raw_metadata = source_catalogue
            try:
                meta = json.loads(raw_metadata)
            except (TypeError, ValueError, RecursionError):
                raise DerivedPublicationConflict("derived_variant_catalogue_invalid") from None
            if type(meta) is not dict:
                raise DerivedPublicationConflict("derived_variant_catalogue_invalid")
            # Supplier edition/provenance remain history of the parent only.
            meta.update({
                "path": str(published_path), "variant": True,
                "source_artifact_id": parent_artifact_id,
                "source_placement_id": parent_placement_id,
                "origin_kind": "derived",
            })
            catalogue_id = storage._stable_id(
                "catalog", placement_id, artifact_id, source_id,
            )
            db.execute(
                "INSERT INTO catalog_entries(catalog_entry_id,artifact_id,placement_id,"
                "title,author,media_format,identity_id,provider_id,item_id,"
                "local_state,metadata_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (catalogue_id, artifact_id, placement_id, str(title or "Sans titre"),
                 author, media_format, identity_id, "cifereca", source_id,
                 "local", dumped(meta), now, now),
            )
        if scope_current is not None and not scope_current(db):
            raise DerivedPublicationConflict("artifact_transform_scope_changed")
        parent_current()
        output_current()
        if journal_intent is not None:
            try:
                commit_derived_reservation_in_transaction(
                    db, journal_reservation, intent=journal_intent,
                    placement_id=placement_id, origin_id=origin_id, recipe_id=recipe_id,
                    **private_options,
                )
            except DerivedJournalConflict as exc:
                raise DerivedPublicationConflict(str(exc)) from None
    return placement_id
