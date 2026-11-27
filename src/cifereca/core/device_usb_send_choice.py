"""Read-only selection of an already materialized reader variant.

The per-reader preference is not a conversion, source authorization or
replacement instruction. The existing derived-placement OriginLink proof
qualifies a different Artifact; title/provider/SHA similarities never do.
Called inside the Storage transaction so an outdated UI choice cannot be
committed after a binding, intent or variant change.
"""
from __future__ import annotations

from .device_usb_reader_formats import declared_usb_reader_formats
from .device_usb_variant_review import _SUFFIX


def select_reader_send(storage, db, *, identity_id, device_id, source_placement_id):
    from ..artifact_runtime import _scoped_derived_preference_candidate

    source = db.execute(
        "SELECT p.artifact_id,a.media_format FROM placements p JOIN artifacts a "
        "ON a.artifact_id=p.artifact_id WHERE p.placement_id=? AND p.identity_id=? "
        "AND p.state='present'", (source_placement_id, identity_id),
    ).fetchone()
    binding = db.execute(
        "SELECT identity_id,metadata_json FROM device_bindings WHERE device_id=?",
        (device_id,),
    ).fetchone()
    if source is None or binding is None or binding[0] != identity_id:
        raise ValueError("reader_send_scope_changed")
    meta = storage._json_load(binding[1])
    if type(meta) is not dict:
        raise ValueError("reader_send_scope_changed")
    preferred = meta.get("reader_preferred_format")
    declaration = declared_usb_reader_formats(
        identity_id=identity_id, device_id=device_id,
        usb_fingerprint=meta.get("usb_fingerprint"),
        binding={"identity_id": identity_id, "device_id": device_id, "metadata": meta},
    )
    if preferred is not None and (
        type(preferred) is not str or declaration is None
        or preferred not in declaration.supported_formats
    ):
        raise ValueError("reader_preference_invalid")

    def saved(artifact):
        row = db.execute(
            "SELECT desired_state,metadata_json FROM device_placements "
            "WHERE device_id=? AND artifact_id=?", (device_id, artifact),
        ).fetchone()
        if row is None:
            return None
        owner = storage._json_load(row[1])
        if (row[0] not in ("keep", "remove") or type(owner) is not dict
                or owner.get("intent_identity_id") != identity_id):
            raise ValueError("reader_send_intent_unverified")
        return row[0]

    base = (source[0], source_placement_id, source[1], saved(source[0]))
    # Exact existing wish wins regardless of subsequent format changes.
    if base[3] is not None:
        return base

    columns = ("variant_id", "artifact_id", "source_artifact_id", "source_sha256",
               "transformer_plugin_id", "transformer_version", "profile_plugin_id",
               "profile_version", "options_fingerprint")
    rows = db.execute(
        "SELECT " + ",".join(columns) + " FROM artifact_variants "
        "WHERE source_artifact_id=? LIMIT 65", (source[0],),
    ).fetchall()
    if len(rows) > 64:
        raise ValueError("reader_send_variants_unbounded")
    variants = {}
    for row in rows:
        recipe = dict(zip(columns, row))
        candidate = _scoped_derived_preference_candidate(
            storage, recipe, identity_id, source_placement_id=source_placement_id, _db=db,
        )
        if candidate is None:
            continue
        artifact = recipe["artifact_id"]
        media = db.execute(
            "SELECT media_format,metadata_json FROM artifacts WHERE artifact_id=?", (artifact,),
        ).fetchone()
        artifact_metadata = storage._json_load(media[1]) if media is not None else None
        if (media is None or type(artifact_metadata) is not dict
                or artifact_metadata.get("artifact_kind") != "file" or media[0] not in _SUFFIX):
            continue
        variants[artifact] = (artifact, candidate[1], media[0], saved(artifact))
    existing = [row for row in variants.values() if row[3] is not None]
    if len(existing) > 1:
        raise ValueError("reader_send_existing_variants_ambiguous")
    if existing:
        return existing[0]
    if preferred is None or source[1] == preferred:
        return base
    matching = [row for row in variants.values() if row[2] == preferred]
    # No guessing between variants: keep the exact clicked local Artifact
    # when the preference cannot be unambiguously satisfied. Core preview
    # still refuses unsupported/protected/missing bytes.
    return matching[0] if len(matching) == 1 else base
