"""Bounded same-source detail facts inside the existing canonical C row.

The original listing member, cover and source proof are immutable here.
Only a trusted already qualified purchase publication may add this extension.
At most one latest detail fact per field; omission never erases a fact.
Not a second source, scheduler, cache, cross-provider link or rights grant.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from .public_search_projection import (
    _public_bibliographic_text, decode_public_classification,
    decode_public_language_observation, decode_public_series_relation,
)

_SCALARS = frozenset(("title", "author", "publisher", "description", "published_at"))
_CLASS = frozenset(("genre", "genres", "subject", "subjects", "tags", "keywords", "themes"))
_FIELDS = _SCALARS | _CLASS | {"languages", "series"}
_REV = re.compile(r"notice-raw\.v1:[0-9a-f]{64}\Z")
_MAX_BYTES = 48 * 1024


@dataclass(frozen=True, slots=True, repr=False)
class CanonicalPublicDetailFact:
    field: str
    value: str | tuple[str, ...]
    provider_id: str
    source_ref: str
    source_revision: str
    observed_at: int
    structured_evidence: str | None = None


def decode_detail_fields(raw, *, provider, source_ref, base_revision, base_observed_at):
    if (type(raw) is not dict or not raw or set(raw) - _FIELDS
            or len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > _MAX_BYTES):
        raise ValueError("corrupt_public_detail_fields")
    facts = []
    for field, wire in sorted(raw.items()):
        if (type(wire) is not dict or set(wire) != {"value", "revision", "at", "structured"}
                or type(wire["revision"]) is not str or not _REV.fullmatch(wire["revision"])
                or wire["revision"] == base_revision
                or type(wire["at"]) is not int
                or not max(1, base_observed_at) <= wire["at"] <= 2**63 - 1):
            raise ValueError("corrupt_public_detail_fields")
        value, structured = wire["value"], wire["structured"]
        if field in _SCALARS:
            if (structured is not None or not _public_bibliographic_text(
                    value, required=True, limit=4000 if field == "description" else 512)):
                raise ValueError("corrupt_public_detail_fields")
        elif field in _CLASS:
            if structured is not None or decode_public_classification({field: value}) is None:
                raise ValueError("corrupt_public_detail_fields")
            if type(value) is list:
                if not any(part.strip() for part in value):
                    raise ValueError("corrupt_public_detail_fields")
                value = tuple(value)
            elif not value.strip():
                raise ValueError("corrupt_public_detail_fields")
        elif field == "languages":
            proof = decode_public_language_observation(structured, provider_id=provider)
            if proof is None or type(value) is not list or tuple(value) != proof.languages:
                raise ValueError("corrupt_public_detail_fields")
            value = tuple(value)
        else:
            proof = decode_public_series_relation(structured, provider_id=provider)
            if proof is None or value != proof.label:
                raise ValueError("corrupt_public_detail_fields")
        facts.append(CanonicalPublicDetailFact(
            field, value, provider, source_ref, wire["revision"], wire["at"],
            None if structured is None else json.dumps(
                structured, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    return tuple(facts)


def retain_purchase_detail_fields_txn(db, source, detail, candidate, comparison, *, now):
    """Only extend a current, already published, exact self C.

    Same transaction as the qualified purchase writer. A changed/missing
    canonical member is not repaired here. No synthetic source epoch or
    source observation. A later actual listing replacement drops the
    extension until the new base proof is separately qualified.
    """
    from .notice_c_cover_index import (
        _db_key, _decode, _json, _exact_public_search_evidence,
        canonical_cover_document_ref, _MAX_BYTES as ROW_MAX, _MAX_EPOCH,
    )
    from .notice_cover_repository import _checked_source_state, _opaque_key
    from .notice_c_purchase_publication import _association
    from .notice_c_purchase_field_delta import compare_exact_purchase_fields
    if (not db.in_transaction or type(now) is not int or detail.observed_at > now
            or source.observation.key != detail.key
            or comparison != compare_exact_purchase_fields(source.observation, detail, candidate)):
        raise ValueError("notice_purchase_detail_fields_changed")
    if detail.source_revision == source.observation.source_revision or detail.observed_at < source.observation.observed_at:
        return False
    key = _opaque_key(source.document)
    _, live, _ = _checked_source_state(db, source.document, key)
    if live != source or not _association(detail, candidate):
        raise ValueError("notice_purchase_detail_fields_changed")
    edition, proof = _exact_public_search_evidence(source)
    document = canonical_cover_document_ref(edition)
    stored = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(document),)).fetchone()
    if stored is None:
        return False
    canonical = _decode(stored[0], document)
    if len(canonical.sources) != 1:
        return False
    member = canonical.sources[0]
    if (member.source_ref != source.document.source_key or member.source_epoch != source.epoch
            or member.source_revision != source.observation.source_revision
            or member.relation_proof_id != proof or not member.public_web_references):
        return False
    payload = _json(stored[0])
    row = payload["sources"][member.source_ref]
    previous = row.get("public_detail_fields", {})
    fields = dict(previous)
    for change in comparison:
        fact = change.detail
        if fact is None or (change.state == "agreement" and change.field not in fields):
            continue
        if (fact.provider_id != member.provider_id or fact.source_ref != member.source_ref
                or fact.source_revision != detail.source_revision or fact.observed_at != detail.observed_at):
            raise ValueError("notice_purchase_detail_fields_changed")
        entry = {"value": list(fact.value) if type(fact.value) is tuple else fact.value,
                 "revision": fact.source_revision, "at": fact.observed_at,
                 "structured": None if fact.structured_evidence is None else json.loads(fact.structured_evidence)}
        old = fields.get(change.field)
        if old is not None and (old["at"] > entry["at"] or (
                old["at"] == entry["at"] and old != entry)):
            continue
        fields[change.field] = entry
    if fields == previous:
        return False
    row["public_detail_fields"] = fields
    if payload["epoch"] >= _MAX_EPOCH:
        raise ValueError("canonical_cover_epoch_overflow")
    payload["epoch"] += 1
    try:
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > ROW_MAX:
            return False
        _decode(encoded, document)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        # No partial field update under a size or validation refusal.
        return False
    cursor = db.execute("UPDATE metadata SET value=? WHERE key=? AND value=?",
                        (encoded, _db_key(document), stored[0]))
    if cursor.rowcount != 1:
        raise ValueError("notice_purchase_detail_fields_changed")
    return True
