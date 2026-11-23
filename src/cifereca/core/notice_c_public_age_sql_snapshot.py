"""#4552: authoritative SQLite snapshot port for admitted public age evidence.

Pass this reader to the descriptive Core coordinator's capture_sources_txn
closure. It reads only an exact Notice C source document in the caller-owned
transaction, with no network, profile mirror, provider or plugin execution.
"""
from __future__ import annotations

from .notice_cover_repository import _checked_source_state, _opaque_key
from .notice_c_public_age_descriptive_facts import public_age_descriptive_facts


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
        key = _opaque_key(document)
        if key is None or document.kind != "public_catalogue":
            raise ValueError("invalid_public_age_snapshot_document")
        if key in seen:
            continue
        seen.add(key)
        for fact in public_age_facts_txn(db, document):
            if fact["edition_id"] == target_edition_id:
                facts.append(fact)
                if len(facts) > 512:
                    raise ValueError("public_age_snapshot_too_large")
    return tuple(facts), {}
