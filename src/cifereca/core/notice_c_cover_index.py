"""Persistent Notice C cover references from explicitly reconciled source notices.

This is a documentary projection, not an image cache or an ISBN matcher.
An authorized Core producer must supply a verified source observation and
its independently attested relationship to the canonical edition. The
record survives eviction of the short-lived provider-source observations.
N2 derives its decisions from this record; N3 owns image bytes/previews.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any

from .book_cover_projection import CoverDocumentScope
from .covers import CoverCandidate, candidate
from .notice_cover_repository import (
    PersistedNoticeCover, _decode as _decode_source,
    _opaque_key as _source_db_key,
)

_PREFIX = "notice.c.cover.canonical.v1:"
_REF_PREFIX = "notice-c:canonical-cover:v1:"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}\Z")
_MAX_SOURCES = 32
_MAX_URLS = 128
_MAX_BYTES = 131072
_MAX_EPOCH = 2**63 - 1


@dataclass(frozen=True, slots=True)
class ConsolidatedCoverSource:
    source_ref: str  # opaque exact source/edition digest
    provider_id: str
    source_revision: str
    observed_at: int
    source_epoch: int
    relation_proof_id: str
    state: str
    urls: tuple[str, ...]
    visibility: str


@dataclass(frozen=True, slots=True)
class ConsolidatedNoticeCover:
    document: CoverDocumentScope
    epoch: int
    sources: tuple[ConsolidatedCoverSource, ...]
    candidates: tuple[CoverCandidate, ...]
    snapshot_complete: bool

    @property
    def attested_book_identity(self) -> str:
        """A canonical-edition relation was explicitly admitted by Core."""
        return self.document.source_key


def canonical_cover_document_ref(canonical_edition_id: str) -> CoverDocumentScope:
    """Stable canonical edition reference, not a title/ISBN/image-URL join."""
    if (type(canonical_edition_id) is not str
            or _IDENTIFIER.fullmatch(canonical_edition_id) is None):
        raise ValueError("invalid_canonical_edition")
    digest = sha256(json.dumps(
        ["cifereca.notice-c.canonical-cover.v1", canonical_edition_id],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")).hexdigest()
    return CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        source_key=_REF_PREFIX + digest,
        provider_id="cifereca.c", credential_id="__public__",
    )


def is_canonical_cover_document(document: CoverDocumentScope) -> bool:
    return (type(document) is CoverDocumentScope
            and document.kind == "public_catalogue"
            and document.identity_id is None
            and document.provider_id == "cifereca.c"
            and document.credential_id == "__public__"
            and document.account_id is None
            and document.source_key.startswith(_REF_PREFIX)
            and _HASH.fullmatch(document.source_key[len(_REF_PREFIX):]) is not None)


def _db_key(document: CoverDocumentScope) -> str:
    if not is_canonical_cover_document(document):
        raise ValueError("invalid_canonical_cover_ref")
    return _PREFIX + document.source_key[len(_REF_PREFIX):]


def _json(raw: object) -> dict[str, Any]:
    if type(raw) is not str or not 0 < len(raw.encode("utf-8")) <= _MAX_BYTES:
        raise ValueError("corrupt_canonical_cover")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("corrupt_canonical_cover")
            result[key] = value
        return result

    try:
        parsed = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError("corrupt_canonical_cover") from exc
    if type(parsed) is not dict or set(parsed) != {
        "schema", "edition_id", "epoch", "sources",
    } or parsed["schema"] != 1 or type(parsed["schema"]) is not int:
        raise ValueError("corrupt_canonical_cover")
    if (type(parsed["edition_id"]) is not str
            or _IDENTIFIER.fullmatch(parsed["edition_id"]) is None
            or type(parsed["epoch"]) is not int
            or not 1 <= parsed["epoch"] <= _MAX_EPOCH
            or type(parsed["sources"]) is not dict
            or not 1 <= len(parsed["sources"]) <= _MAX_SOURCES):
        raise ValueError("corrupt_canonical_cover")
    return parsed


def _decode(raw: object, document: CoverDocumentScope) -> ConsolidatedNoticeCover:
    parsed = _json(raw)
    if canonical_cover_document_ref(parsed["edition_id"]) != document:
        raise ValueError("corrupt_canonical_cover")
    sources: list[ConsolidatedCoverSource] = []
    covers: list[CoverCandidate] = []
    for ref, item in sorted(parsed["sources"].items()):
        if (type(ref) is not str or not ref.startswith("notice-c:v1:")
                or _HASH.fullmatch(ref[len("notice-c:v1:"):]) is None
                or type(item) is not dict or set(item) != {
                    "provider", "source_revision", "observed_at",
                    "source_epoch", "proof", "state", "urls", "visibility",
                }):
            raise ValueError("corrupt_canonical_cover")
        provider, revision, proof = (
            item["provider"], item["source_revision"], item["proof"],
        )
        if (any(type(x) is not str or _IDENTIFIER.fullmatch(x) is None
                for x in (provider, revision, proof))
                or type(item["observed_at"]) is not int
                or not 0 <= item["observed_at"] <= _MAX_EPOCH
                or type(item["source_epoch"]) is not int
                or not 1 <= item["source_epoch"] <= _MAX_EPOCH
                or item["state"] not in ("available", "missing", "pending")
                or item["visibility"] not in ("public", "private")
                or type(item["urls"]) is not list
                or len(item["urls"]) > 32
                or (item["visibility"] == "private" and item["urls"])):
            raise ValueError("corrupt_canonical_cover")
        urls = []
        for url in item["urls"]:
            if type(url) is not str:
                raise ValueError("corrupt_canonical_cover")
            proof_candidate = candidate(
                url, tier="catalog", source=provider, evidence_id=ref,
            )
            if (proof_candidate is None
                    or not proof_candidate.url.startswith("https://")):
                raise ValueError("corrupt_canonical_cover")
            urls.append(url)
            covers.append(proof_candidate)
            if len(covers) > _MAX_URLS:
                raise ValueError("corrupt_canonical_cover")
        if (item["state"] == "available" and not urls
                or item["state"] == "missing" and urls):
            raise ValueError("corrupt_canonical_cover")
        sources.append(ConsolidatedCoverSource(
            source_ref=ref, provider_id=provider,
            source_revision=revision, observed_at=item["observed_at"],
            source_epoch=item["source_epoch"],
            relation_proof_id=proof, state=item["state"],
            urls=tuple(urls), visibility=item["visibility"],
        ))
    # Any admitted positive public cover can be displayed, while a complete
    # lack of candidates is NOT 'missing' if sources remain unresolved/private.
    complete = bool(covers) or all(
        x.visibility == "public" and x.state != "pending" for x in sources
    )
    return ConsolidatedNoticeCover(
        document, parsed["epoch"], tuple(sources), tuple(covers), complete,
    )


def read_consolidated_notice_cover(
    storage: Any, document: CoverDocumentScope,
) -> ConsolidatedNoticeCover | None:
    """One indexed SQLite read; no provider traversal, reparse or image IO."""
    if not is_canonical_cover_document(document):
        return None
    with storage._lock, storage._connect() as db:
        row = db.execute(
            "SELECT value FROM metadata WHERE key=?", (_db_key(document),),
        ).fetchone()
    return _decode(row[0], document) if row else None


def persist_consolidated_cover_source(
    storage: Any, *, canonical_edition_id: str,
    source: PersistedNoticeCover, relation_proof_id: str,
    expected_epoch: int | None = None,
) -> ConsolidatedNoticeCover:
    """Upsert ONE verified provider contribution without erasing others.

    Only a Core canonical-identity producer may call this with a qualified
    relation_proof_id; this method deliberately makes no inference from title,
    ISBN, cover URL or similarly named providers. The source must still be
    present and current in the exact SQLite source index at write time.
    Public safe URLs are copied; private sources retain only opaque locators
    until an authorized, session-aware resolver is implemented.
    """
    document = canonical_cover_document_ref(canonical_edition_id)
    if (type(source) is not PersistedNoticeCover
            or type(relation_proof_id) is not str
            or _IDENTIFIER.fullmatch(relation_proof_id) is None):
        raise ValueError("invalid_canonical_cover_evidence")
    if (expected_epoch is not None
            and (type(expected_epoch) is not int
                 or not 0 <= expected_epoch < _MAX_EPOCH)):
        raise ValueError("invalid_canonical_cover_epoch")
    source_document = source.document
    source_key = _source_db_key(source_document)
    if source_key is None:
        raise ValueError("invalid_canonical_cover_source")
    source_ref = source_document.source_key
    visibility = source.observation.key.visibility
    if visibility not in ("public", "private"):
        raise ValueError("invalid_canonical_cover_source")

    key = _db_key(document)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        original = db.execute(
            "SELECT value FROM metadata WHERE key=?", (source_key,),
        ).fetchone()
        if original is None or _decode_source(
            original[0], source_document,
        ) != source:
            raise ValueError("stale_canonical_cover_source")
        old = db.execute(
            "SELECT value FROM metadata WHERE key=?", (key,),
        ).fetchone()
        previous = _json(old[0]) if old else None
        if previous is not None and canonical_cover_document_ref(
            previous["edition_id"],
        ) != document:
            raise ValueError("corrupt_canonical_cover")
        epoch = previous["epoch"] if previous else 0
        if expected_epoch is not None and expected_epoch != epoch:
            raise ValueError("canonical_cover_epoch_changed")
        rows = dict(previous["sources"]) if previous else {}
        # A partial parser may expose a candidate while declaring its
        # inventory incomplete. Preserve the source's pending provenance,
        # but never promote that incomplete cover to an N1 decision.
        urls = (
            [x.url for x in source.attribute.candidates]
            if visibility == "public"
            and source.attribute.state == "available" else []
        )
        incoming = {
            "provider": source.observation.key.provider_id,
            "source_revision": source.observation.source_revision,
            "observed_at": source.observation.observed_at,
            "source_epoch": source.epoch,
            "proof": relation_proof_id,
            "state": (
                source.attribute.state if visibility == "public"
                else "pending"
            ),
            "urls": urls,
            "visibility": visibility,
        }
        if rows.get(source_ref) == incoming:
            return _decode(old[0], document)
        if len(rows) >= _MAX_SOURCES and source_ref not in rows:
            raise ValueError("canonical_cover_sources_overflow")
        rows[source_ref] = incoming
        payload = {
            "schema": 1, "edition_id": canonical_edition_id,
            "epoch": epoch + 1, "sources": rows,
        }
        if payload["epoch"] > _MAX_EPOCH:
            raise ValueError("canonical_cover_epoch_overflow")
        raw = json.dumps(
            payload, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        if len(raw.encode("utf-8")) > _MAX_BYTES:
            raise ValueError("canonical_cover_too_large")
        admitted = _decode(raw, document)
        db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, raw),
        )
        return admitted


__all__ = [
    "ConsolidatedCoverSource", "ConsolidatedNoticeCover",
    "canonical_cover_document_ref", "is_canonical_cover_document",
    "read_consolidated_notice_cover", "persist_consolidated_cover_source",
]
