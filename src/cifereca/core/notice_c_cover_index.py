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
class ConsolidatedPublicBibliography:
    """Validated public facts retained with a canonical C source after TTL."""

    edition_id: str
    record_id: str
    docbase: str
    medium: str
    source_revision: str | None
    title: str
    author: str | None
    publisher: str | None
    media_format: str | None
    description: str | None
    last_seen_at: int


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
    public_bibliography: ConsolidatedPublicBibliography | None = None


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


_BIB_KEYS = frozenset((
    "edition_id", "record_id", "docbase", "medium", "source_revision",
    "title", "author", "publisher", "media_format", "description",
    "last_seen_at",
))


def _decode_public_bibliography(
    raw: object, *, provider: str, source_ref: str, observed_at: int,
) -> ConsolidatedPublicBibliography:
    """Fail closed: source ref must rederive from the published bibliography.

    Neither a title, an ISBN nor an image reference can bind an edition.
    An old canonical row retains this immutable exact-source coordinate even
    after its short-lived observation row has been purged.
    """
    from .notice_search_cover_producer import _coordinate_digest
    from .notice_source_observation import NoticeSourceKey
    from .notice_cover_projection import notice_cover_document_ref
    from .public_search_projection import _public_bibliographic_text

    if type(raw) is not dict or set(raw) != _BIB_KEYS:
        raise ValueError("corrupt_canonical_cover")
    def coord(value, *, optional=False):
        return (value is None and optional) or (
            type(value) is str and _IDENTIFIER.fullmatch(value) is not None
            and "://" not in value and ".." not in value
        )
    if (not coord(raw["edition_id"])
            or not coord(raw["record_id"])
            or not coord(raw["docbase"])
            or not coord(raw["source_revision"], optional=True)
            or raw["medium"] not in ("digital", "physical", "unknown")
            or type(raw["last_seen_at"]) is not int
            or not observed_at <= raw["last_seen_at"] <= _MAX_EPOCH):
        raise ValueError("corrupt_canonical_cover")

    for name in ("title", "author", "publisher", "media_format", "description"):
        value = raw[name]
        if value is None:
            if name == "title":
                raise ValueError("corrupt_canonical_cover")
            continue
        if (not _public_bibliographic_text(
                    value, required=name == "title",
                    limit=4000 if name == "description" else 512,
                )
                or value.lstrip().casefold().startswith(("/", "\\", "~/", "file:"))):
            raise ValueError("corrupt_canonical_cover")

    expected_edition = "edition:" + _coordinate_digest(
        provider, "public_catalog", raw["record_id"],
        raw["docbase"], raw["medium"], raw["source_revision"],
    )
    if raw["edition_id"] != expected_edition:
        raise ValueError("corrupt_canonical_cover")
    role = {
        "digital": "digital", "physical": "print", "unknown": "unknown",
    }[raw["medium"]]
    key = NoticeSourceKey(
        provider_id=provider, visibility="public", identity_id=None,
        credential_id=None, account_id=None,
        branch_id="search.public.catalogue",
        record_id="search:" + _coordinate_digest(
            provider, "public_catalog", raw["record_id"],
        ),
        edition_id=expected_edition, manifestation_role=role,
    )
    if notice_cover_document_ref(key).source_key != source_ref:
        raise ValueError("corrupt_canonical_cover")
    return ConsolidatedPublicBibliography(**raw)


def _decode(raw: object, document: CoverDocumentScope) -> ConsolidatedNoticeCover:
    parsed = _json(raw)
    if canonical_cover_document_ref(parsed["edition_id"]) != document:
        raise ValueError("corrupt_canonical_cover")
    sources: list[ConsolidatedCoverSource] = []
    covers: list[CoverCandidate] = []
    for ref, item in sorted(parsed["sources"].items()):
        if (type(ref) is not str or not ref.startswith("notice-c:v1:")
                or _HASH.fullmatch(ref[len("notice-c:v1:"):]) is None
                or type(item) is not dict or set(item) not in (
                    {"provider", "source_revision", "observed_at",
                     "source_epoch", "proof", "state", "urls", "visibility"},
                    {"provider", "source_revision", "observed_at",
                     "source_epoch", "proof", "state", "urls", "visibility",
                     "public_bibliography"},
                )):
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
        biblio = None
        if "public_bibliography" in item:
            if item["visibility"] != "public":
                raise ValueError("corrupt_canonical_cover")
            biblio = _decode_public_bibliography(
                item["public_bibliography"], provider=provider,
                source_ref=ref, observed_at=item["observed_at"],
            )
        sources.append(ConsolidatedCoverSource(
            source_ref=ref, provider_id=provider,
            source_revision=revision, observed_at=item["observed_at"],
            source_epoch=item["source_epoch"],
            relation_proof_id=proof, state=item["state"],
            urls=tuple(urls), visibility=item["visibility"],
            public_bibliography=biblio,
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
        # Copy the already validated public bibliographic *source fact*
        # alongside its canonical cover record, in the same SQLite commit.
        # The short-lived provider row may later be purged independently.
        # Private records and generic cover-only observations never enter
        # this durable descriptive snapshot.
        if (visibility == "public"
                and source.observation.parser_id == "core.search.public-cover"
                and source.observation.parser_version == "2"):
            from .notice_c_public_search import _public_observation
            hit = _public_observation(
                original[0], key_string=source_key,
                now=source.observation.observed_at,
            )
            if hit is not None and hit.document == source_document:
                incoming["public_bibliography"] = {
                    "edition_id": hit.edition_id,
                    "record_id": hit.record_id,
                    "docbase": hit.docbase,
                    "medium": hit.medium,
                    "source_revision": hit.source_revision,
                    "title": hit.title,
                    "author": hit.author,
                    "publisher": hit.publisher,
                    "media_format": hit.media_format,
                    "description": hit.description,
                    "last_seen_at": hit.last_seen_at,
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
