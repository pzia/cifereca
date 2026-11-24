"""#4552: authoritative SQLite snapshot port for admitted public age evidence.

Pass this reader to the descriptive Core coordinator's capture_sources_txn
closure. It reads only an exact Notice C source document in the caller-owned
transaction, with no network, profile mirror, provider or plugin execution.
"""
from __future__ import annotations

from .notice_cover_repository import _checked_source_state, _opaque_key, _source_fingerprint
from .notice_c_public_age_descriptive_facts import public_age_descriptive_facts, _encode_age_facts


def public_age_facts_txn(db, document):
    """Return native age facts from one validated, exact persisted C source.

    Invalid, missing, evicted or tampered rows abstain; never synthesize a
    source fact from a title, ISBN, URL or other edition.
    """
    try:
        key = _opaque_key(document)
        if key is None or document.kind != "public_catalogue":
            return ()
        _, persisted, _ = _checked_source_state(db, document, key)
        if persisted is None:
            return ()
        return public_age_descriptive_facts(persisted.observation)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        return ()


def _retained_exact_age_facts_txn(db, document, target_edition_id):
    """Only qualified eviction of the exact current self C permits fallback."""
    from .notice_c_cover_index import canonical_cover_document_ref, _db_key, _decode
    from .notice_search_cover_producer import _coordinate_digest
    from hashlib import sha256
    import json

    try:
        _, live, fence = _checked_source_state(db, document, _opaque_key(document))
        if live is not None or fence is None or fence.get("evicted") is not True:
            return ()
        canonical = canonical_cover_document_ref(target_edition_id)
        raw = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(canonical),)).fetchone()
        if raw is None:
            return ()
        members = _decode(raw[0], canonical).sources
        for member in members:
            bio = member.public_bibliography
            if (member.visibility != "public" or member.source_ref != document.source_key
                    or member.provider_id != document.provider_id or bio is None
                    or bio.edition_id != target_edition_id or bio.age_observation is None):
                continue
            proof = sha256(json.dumps(
                ["cifereca.notice-c.exact-public-search-source.v1",
                 member.source_ref, target_edition_id],
                ensure_ascii=True, separators=(",", ":"),
            ).encode("ascii")).hexdigest()
            if (member.relation_proof_id != "core.exact.search.v1:" + proof
                    or fence["epoch"] != member.source_epoch + 1
                    or fence["proof"] not in {
                        _source_fingerprint(member, ("search_result", "thumbnail_url"), complete)
                        for complete in (True, False)
                    }):
                return ()
            record_id = "search:" + _coordinate_digest(
                member.provider_id, "public_catalog", bio.record_id,
            )
            return _encode_age_facts(
                bio.age_observation, provider_id=member.provider_id, record_id=record_id,
                edition_id=bio.edition_id, revision=member.source_revision,
            )
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        return ()
    return ()


def capture_public_age_sources_txn(db, *, documents, target_edition_id):
    """Coordinator-compatible authoritative snapshot of exact public sources.

    The caller must obtain documents from a vetted Core source inventory.
    No strong relation is inferred: foreign editions are rejected by the
    existing admission gate unless a separate attested-link producer exists.
    A bounded, duplicate-free snapshot prevents arbitrary catalogue scans.
    """
    if (type(documents) not in (tuple, list) or len(documents) > 128
            or type(target_edition_id) is not str
            or not target_edition_id or len(target_edition_id) > 160):
        raise ValueError("invalid_public_age_snapshot_scope")
    facts = []
    seen = set()
    for document in documents:
        try:
            key = _opaque_key(document)
        except (TypeError, AttributeError, ValueError) as exc:
            raise ValueError("invalid_public_age_snapshot_document") from exc
        if key is None or document.kind != "public_catalogue":
            raise ValueError("invalid_public_age_snapshot_document")
        if key in seen:
            continue
        seen.add(key)
        current = public_age_facts_txn(db, document)
        if not current:
            current = _retained_exact_age_facts_txn(db, document, target_edition_id)
        for fact in current:
            if fact["edition_id"] == target_edition_id:
                facts.append(fact)
                if len(facts) > 512:
                    raise ValueError("public_age_snapshot_too_large")
    return tuple(facts), {}
