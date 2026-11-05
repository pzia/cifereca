"""P1 #4520: one atomic SQLite publication of a locally derived variant.

The filesystem is published exclusively by artifact_runtime *before* this
transaction. A failed SQL step cannot assert a partial placement/recipe/
origin. Orphan file recovery after a crash remains a separate bounded step.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any


class DerivedPublicationConflict(ValueError):
    """Scoped refusal: do not reinterpret a local variant as a supplier right."""


def publish_derived_variant(
    storage: Any, *, parent_artifact_id: str, parent_sha256: str,
    parent_placement_id: str, parent_path: Path, identity_id: str,
    published_path: Path, output_sha256: str, output_size: int,
    media_format: str, mime_type: str | None, variant_kind: str,
    transformer_id: str, transformer_version: str,
    profile_id: str | None, profile_version: str,
    options_fingerprint: str, book_id: str | None,
    warnings: tuple[str, ...] | list[str], facts: dict[str, Any],
    expected_intent_key: str = "", expected_intent_sha256: str = "",
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
            or not str(parent_artifact_id).startswith("sha256:")
            or not str(output_sha256).isalnum()
            or len(output_sha256) != 64
            or not parent_path.is_absolute() or not published_path.is_absolute()
            or published_path == parent_path):
        raise DerivedPublicationConflict("derived_variant_invalid_coordinates")
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

    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Explicit crash recovery only: bind the approved receipt to the
        # durable pre-hardlink intent while holding the SAME SQLite writer
        # transaction that will attest derived provenance. Ordinary fresh
        # publications do not need this optional checkpoint comparison.
        if expected_intent_key or expected_intent_sha256:
            if (not expected_intent_key.startswith("derived.publication.pending.v1:")
                    or type(expected_intent_sha256) is not str
                    or len(expected_intent_sha256) != 64
                    or db.execute(
                        "SELECT value FROM metadata WHERE key=?",
                        (expected_intent_key,),
                    ).fetchone() != (expected_intent_sha256,)):
                raise DerivedPublicationConflict("derived_variant_intent_mismatch")
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
    return placement_id
