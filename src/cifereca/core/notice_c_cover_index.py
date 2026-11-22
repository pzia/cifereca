"""Persistent Notice C cover references from explicitly reconciled source notices.

This is a documentary projection, not an image cache or an ISBN matcher.
An authorized Core producer must supply a verified source observation and
its independently attested relationship to the canonical edition. The
record survives eviction of the short-lived provider-source observations.
N2 derives its decisions from this record; N3 owns image bytes/previews.
"""
from __future__ import annotations

from .public_search_projection import PublicSeriesRelation, PublicLanguageObservation, PublicClassification
from .notice_c_public_age import PublicAgeObservation, decode_public_age_observation

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Any

from .book_cover_projection import CoverDocumentScope
from .covers import CoverCandidate, candidate
from .notice_c_web_references import (CatalogueWebReference, FIELD as _WEB_FIELD,
    decode_public_web_references, qualified_public_web_source)
from .notice_cover_repository import (
    PersistedNoticeCover, _checked_source_state,
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
    published_at: str | None = None
    series_relation: PublicSeriesRelation | None = None
    language_observation: PublicLanguageObservation | None = None
    classification: PublicClassification | None = None
    age_observation: PublicAgeObservation | None = None


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
    public_web_references: tuple[CatalogueWebReference, ...] = ()
    public_detail_facts: tuple = ()  # bounded typed alternatives for this exact source
    private_web_digest: str | None = None  # opaque private field integrity, never URL/scope
    private_series_digest: str | None = None  # credential-scoped sidecar


@dataclass(frozen=True, slots=True)
class ConsolidatedNoticeCover:
    document: CoverDocumentScope
    epoch: int
    sources: tuple[ConsolidatedCoverSource, ...]
    candidates: tuple[CoverCandidate, ...]
    snapshot_complete: bool

    @property
    def catalogue_web_references(self) -> tuple[CatalogueWebReference, ...]:
        # Source order is stable; do not merge identical URLs across providers.
        return tuple(ref for source in self.sources for ref in source.public_web_references)

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
    # A frozen CoverDocumentScope is NOT a trust boundary: a view/plugin can
    # change its dataclass fields with object.__setattr__. Never invoke a
    # foreign __eq__, .startswith or a hash while checking C authority.
    if type(document) is not CoverDocumentScope:
        return False
    for name in ("kind", "source_key", "provider_id", "credential_id",
                 "loan_id", "artifact_id", "placement_id"):
        value = getattr(document, name)
        if type(value) is not str or len(value) > 512 or not value.isprintable():
            return False
    return (document.kind == "public_catalogue"
            and document.identity_id is None
            and document.provider_id == "cifereca.c"
            and document.credential_id == "__public__"
            and document.account_id is None
            and not document.loan_id and not document.artifact_id
            and not document.placement_id
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
    from .public_search_projection import (
        _public_bibliographic_text, decode_public_series_relation,
        decode_public_language_observation,
        decode_public_classification,
    )

    if (type(raw) is not dict or not _BIB_KEYS <= set(raw)
            or set(raw) - _BIB_KEYS - {"published_at", "series_relation", "language_observation", "classification", "age_observation"}):
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

    for name in ("title", "author", "publisher", "media_format", "description", "published_at"):
        value = raw.get(name)
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
    values = dict(raw)
    if "series_relation" in values:
        relation = decode_public_series_relation(values["series_relation"], provider_id=provider)
        if relation is None:
            raise ValueError("corrupt_canonical_cover")
        values["series_relation"] = relation
    if "language_observation" in values:
        language = decode_public_language_observation(
            values["language_observation"], provider_id=provider,
        )
        if language is None:
            raise ValueError("corrupt_canonical_cover")
        values["language_observation"] = language
    if "classification" in values:
        classification = decode_public_classification(values["classification"])
        if classification is None:
            raise ValueError("corrupt_canonical_cover")
        values["classification"] = classification
    if "age_observation" in values:
        age = decode_public_age_observation(
            values["age_observation"], provider_id=provider,
            record_id=raw["record_id"], docbase=raw["docbase"],
            medium=raw["medium"], source_revision=raw["source_revision"],
        )
        if age is None:
            raise ValueError("corrupt_canonical_cover")
        values["age_observation"] = age
    return ConsolidatedPublicBibliography(**values)


def _decode(raw: object, document: CoverDocumentScope) -> ConsolidatedNoticeCover:
    parsed = _json(raw)
    if canonical_cover_document_ref(parsed["edition_id"]) != document:
        raise ValueError("corrupt_canonical_cover")
    sources: list[ConsolidatedCoverSource] = []
    covers: list[CoverCandidate] = []
    for ref, item in sorted(parsed["sources"].items()):
        if (type(ref) is not str or not ref.startswith("notice-c:v1:")
                or _HASH.fullmatch(ref[len("notice-c:v1:"):]) is None
                or type(item) is not dict or (set(item) - {"public_detail_fields", "private_web_digest", "private_series_digest"}) not in (
                    {"provider", "source_revision", "observed_at",
                     "source_epoch", "proof", "state", "urls", "visibility"},
                    {"provider", "source_revision", "observed_at",
                     "source_epoch", "proof", "state", "urls", "visibility",
                     "public_bibliography"},
                    {"provider", "source_revision", "observed_at",
                     "source_epoch", "proof", "state", "urls", "visibility",
                     "public_bibliography", _WEB_FIELD},
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
        web_refs = ()
        if _WEB_FIELD in item:
            if biblio is None or item["visibility"] != "public":
                raise ValueError("corrupt_canonical_cover")
            try:
                web_refs = decode_public_web_references(
                    item[_WEB_FIELD], provider_id=provider, source_ref=ref,
                    record_id=biblio.record_id, edition_id=biblio.edition_id,
                    source_revision=revision, observed_at=item["observed_at"],
                )
            except ValueError:
                raise ValueError("corrupt_canonical_cover") from None
        private_digest = item.get("private_web_digest")
        if "private_web_digest" in item and (
            item["visibility"] != "private" or type(private_digest) is not str
            or _HASH.fullmatch(private_digest) is None
        ):
            raise ValueError("corrupt_canonical_cover")
        series_digest = item.get("private_series_digest")
        if "private_series_digest" in item and (
            item["visibility"] != "private" or type(series_digest) is not str
            or _HASH.fullmatch(series_digest) is None
        ):
            raise ValueError("corrupt_canonical_cover")
        detail_facts = ()
        if "public_detail_fields" in item:
            if biblio is None or not web_refs or item["visibility"] != "public":
                raise ValueError("corrupt_canonical_cover")
            from .notice_c_purchase_detail_fields import decode_detail_fields
            detail_facts = decode_detail_fields(
                item["public_detail_fields"], provider=provider, source_ref=ref,
                base_revision=revision, base_observed_at=item["observed_at"],
            )
        sources.append(ConsolidatedCoverSource(
            source_ref=ref, provider_id=provider,
            source_revision=revision, observed_at=item["observed_at"],
            source_epoch=item["source_epoch"],
            relation_proof_id=proof, state=item["state"],
            urls=tuple(urls), visibility=item["visibility"],
            public_bibliography=biblio, public_web_references=web_refs,
            public_detail_facts=detail_facts, private_web_digest=private_digest,
            private_series_digest=series_digest,
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


def read_current_consolidated_public_cover(storage, document):
    """Cold N2 read; reject a superseded contribution before selecting N3.

    Keep canonical retention independent of raw observation deletion. The
    durable source epoch detects a newer observation even after eviction.
    Private source references are never opened by this shared public read.
    """
    from .notice_cover_repository import _opaque_key, _checked_source_state
    if not is_canonical_cover_document(document):
        return None
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        row = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(document),)).fetchone()
        if row is None:
            return None
        snapshot = _decode(row[0], document)
        for member in snapshot.sources:
            if member.visibility != "public":
                continue
            source_doc = CoverDocumentScope(
                kind="public_catalogue", identity_id=None,
                source_key=member.source_ref, provider_id=member.provider_id,
                credential_id="__public__",
            )
            key = _opaque_key(source_doc)
            if key is None:
                return None
            try:
                _, live, fence = _checked_source_state(db, source_doc, key)
            except ValueError:
                return None
            if live is not None:
                if (live.epoch != member.source_epoch
                        or live.observation.source_revision != member.source_revision):
                    return None
            elif fence is not None:
                expected = member.source_epoch + int(fence.get("evicted") is True)
                if fence["epoch"] != expected:
                    return None
        return snapshot


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
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return _persist_consolidated_cover_source_txn(
            db, canonical_edition_id=canonical_edition_id, source=source,
            relation_proof_id=relation_proof_id, expected_epoch=expected_epoch,
        )


def _persist_consolidated_cover_source_txn(
    db: Any, *, canonical_edition_id: str,
    source: PersistedNoticeCover, relation_proof_id: str,
    expected_epoch: int | None = None,
) -> ConsolidatedNoticeCover:
    """Identical source validation and CAS inside the caller's transaction."""
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
    # The source row and its durable CAS fence must agree in the SAME
    # writer transaction. A readable row alone is not current evidence.
    original, current, _fence = _checked_source_state(
        db, source_document, source_key,
    )
    if current is None or current != source:
        raise ValueError("stale_canonical_cover_source")
    private_web_digest = private_series_digest = None
    if visibility == "private":
        from .notice_c_authenticated_web_retention import retain_private_web_field_txn
        private_web_digest = retain_private_web_field_txn(db, current, json.loads(original[0]))
        from .notice_c_private_series import retain_series_txn
        private_series_digest = retain_series_txn(db, current, json.loads(original[0]))
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
    if private_web_digest is not None:
        incoming["private_web_digest"] = private_web_digest
    if private_series_digest is not None:
        incoming["private_series_digest"] = private_series_digest
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
                # The canonical durable snapshot tracks the timestamp
                # of the actual published *content observation*, not
                # short-lived provider cache HITs. Otherwise a replay
                # of unchanged C would churn the N2 epoch and wrongly
                # make an old source look freshly re-parsed.
                "last_seen_at": source.observation.observed_at,
            }
            # Optional additive field: do not churn old rows which never
            # observed a date. Preserve the literal attested supplier text,
            # not an inferred year/edition or the observation timestamp.
            if hit.published_at is not None:
                incoming["public_bibliography"]["published_at"] = hit.published_at
            if hit.series_relation is not None:
                incoming["public_bibliography"]["series_relation"] = asdict(hit.series_relation)
            if hit.language_observation is not None:
                incoming["public_bibliography"]["language_observation"] = hit.language_observation.wire()
            if hit.classification is not None:
                incoming["public_bibliography"]["classification"] = hit.classification.wire()
            if hit.age_observation is not None:
                incoming["public_bibliography"]["age_observation"] = hit.age_observation.wire()
    # The extension belongs to this exact validated public source, not
    # the per-profile mirror or a different member of the canonical group.
    source_fields = source.observation.fields_copy().get("search_result")
    if type(source_fields) is dict and _WEB_FIELD in source_fields:
        if ("public_bibliography" not in incoming
                or not qualified_public_web_source(
                    source, inventory_complete=json.loads(original[0])["cover_inventory_complete"],
                )):
            raise ValueError("invalid_public_web_references")
        incoming[_WEB_FIELD] = source_fields[_WEB_FIELD]
        # Final _decode below validates the closed shape and URL again
        # before the atomic write. No URL copied from another source.
    # Preserve detail evidence only while the exact original contribution
    # and relation proof remain unchanged. A replaced source must reattest.
    prior_member = rows.get(source_ref)
    if (type(prior_member) is dict and "public_detail_fields" in prior_member
            and {k: v for k, v in prior_member.items() if k != "public_detail_fields"} == incoming):
        incoming["public_detail_fields"] = prior_member["public_detail_fields"]
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


def persist_attested_public_cover_group(
    storage: Any, *, canonical_edition_id: str,
    sources: tuple[PersistedNoticeCover, ...], relation_proof_id: str,
) -> ConsolidatedNoticeCover:
    """Commit an already proven exact public group atomically, or nothing.

    The caller establishes the relationship; this writer revalidates every
    source in one transaction. Never admit an unexpected old membership or
    publish a partially updated group if a later member has changed.
    """
    refs = _public_group_refs(sources)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return _persist_public_group_txn(
            db, canonical_edition_id=canonical_edition_id, sources=sources,
            relation_proof_id=relation_proof_id, refs=refs,
        )


def _public_group_refs(sources: tuple[PersistedNoticeCover, ...]) -> set[str]:
    if (type(sources) is not tuple or not 2 <= len(sources) <= _MAX_SOURCES
            or any(type(source) is not PersistedNoticeCover
                   or source.observation.key.visibility != "public"
                   or source.observation.parser_id != "core.search.public-cover"
                   or source.observation.parser_version != "2"
                   for source in sources)):
        raise ValueError("invalid_canonical_cover_group")
    refs = {source.document.source_key for source in sources}
    if len(refs) != len(sources):
        raise ValueError("invalid_canonical_cover_group")
    return refs


def _persist_public_group_txn(
    db: Any, *, canonical_edition_id: str,
    sources: tuple[PersistedNoticeCover, ...], relation_proof_id: str,
    refs: set[str],
) -> ConsolidatedNoticeCover:
    document = canonical_cover_document_ref(canonical_edition_id)
    old = db.execute("SELECT value FROM metadata WHERE key=?",
                     (_db_key(document),)).fetchone()
    if old is not None:
        previous = _decode(old[0], document)
        if any(member.source_ref not in refs
               or member.visibility != "public"
               or member.relation_proof_id != relation_proof_id
               for member in previous.sources):
            raise ValueError("canonical_cover_group_conflict")
    for source in sources:
        complete = _persist_consolidated_cover_source_txn(
            db, canonical_edition_id=canonical_edition_id, source=source,
            relation_proof_id=relation_proof_id,
        )
    if ({member.source_ref for member in complete.sources} != refs
            or any(member.visibility != "public"
                   or member.public_bibliography is None
                   or member.relation_proof_id != relation_proof_id
                   for member in complete.sources)):
        raise ValueError("canonical_cover_group_conflict")
    return complete


def persist_origin_linked_public_cover_group(
    storage: Any, *, sources: tuple[PersistedNoticeCover, ...],
) -> ConsolidatedNoticeCover:
    """Extend ONE already attested OriginLink group without changing its ref.

    Reattest all exact group members under the owning writer transaction.
    The legacy producer's earlier discovery is only a candidate snapshot,
    not a lease on the OriginLink graph. A disjoint group has a deterministic
    public-only initial id; overlapping or shrinking groups do not create
    a second competing C. The internal transaction port remains available
    to callers that already reattest the group in that same transaction.
    """
    from .notice_c_origin_link_producer import reattest_exact_origin_group_txn
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = reattest_exact_origin_group_txn(db, sources)
        return _persist_origin_linked_public_cover_group_txn(db, sources=current)


def _resolve_origin_linked_public_group_txn(
    db: Any, *, sources: tuple[PersistedNoticeCover, ...],
    expected_document: CoverDocumentScope | None = None,
) -> tuple[str, set[str], str]:
    """Resolve the same conflict-checked group target without a write."""
    if not db.in_transaction:
        raise ValueError("canonical_cover_transaction_required")
    refs = _public_group_refs(sources)
    digest = sha256(json.dumps(
        ["cifereca.c.origin-linked-public-edition.v1", *sorted(refs)],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")).hexdigest()
    prefix = "edition:origin-linked:v1:"
    proof_prefix = "core.origin.linked.v1:"
    initial = prefix + digest
    target = initial
    cursor = db.execute(
        "SELECT m.key,m.value FROM metadata AS m "
        "WHERE m.key>=? AND m.key<? AND EXISTS ("
        "SELECT 1 FROM json_each(CASE WHEN json_valid(m.value) "
        "THEN m.value ELSE '{}' END, '$.sources') AS src "
        "WHERE src.key IN (SELECT value FROM json_each(?))) ORDER BY m.key",
        (_PREFIX, _PREFIX + "\uffff", json.dumps(sorted(refs))),
    )
    overlaps = set()
    for key, raw in cursor:
        payload = _json(raw)
        edition = payload["edition_id"]
        document = canonical_cover_document_ref(edition)
        if _db_key(document) != key:
            raise ValueError("canonical_cover_group_conflict")
        previous = _decode(raw, document)
        members = previous.sources
        if (len(members) == 1
                and members[0].public_bibliography is not None
                and edition == members[0].public_bibliography.edition_id):
            # The independent exact self-C is not a competing relation.
            continue
        suffix = edition[len(prefix):] if edition.startswith(prefix) else ""
        if (_HASH.fullmatch(suffix) is None
                or (len(members) == 1 and edition != initial)
                or any(member.source_ref not in refs
                       or member.visibility != "public"
                       or member.public_bibliography is None
                       or member.relation_proof_id != proof_prefix + suffix
                       for member in members)):
            raise ValueError("canonical_cover_group_conflict")
        overlaps.add(edition)
        if len(overlaps) > 1:
            raise ValueError("canonical_cover_group_conflict")
    if overlaps:
        target = next(iter(overlaps))
    if expected_document is not None and canonical_cover_document_ref(target) != expected_document:
        raise ValueError("canonical_cover_group_conflict")
    return target, refs, proof_prefix + target[len(prefix):]



def _persist_origin_linked_public_cover_group_txn(
    db: Any, *, sources: tuple[PersistedNoticeCover, ...],
    expected_document: CoverDocumentScope | None = None,
) -> ConsolidatedNoticeCover:
    """Same group writer inside the caller's publication transaction."""
    target, refs, proof = _resolve_origin_linked_public_group_txn(
        db, sources=sources, expected_document=expected_document,
    )
    return _persist_public_group_txn(
        db, canonical_edition_id=target, sources=sources, refs=refs,
        relation_proof_id=proof,
    )

def persist_exact_public_search_cover(
    storage: Any, source: PersistedNoticeCover,
) -> ConsolidatedNoticeCover:
    """Publish an exactly sourced public Search C cover under its own edition.

    The live Core search parser already established the anonymous same-call
    PublicSearchNotice before persisting the exact source. Here the source's
    *own* edition key (not its title, ISBN, URL, or another provider's key)
    is the only allowed canonical target. Cross-source relations must be
    admitted by a separate strong-link producer, never by this function.
    Revalidate current SQLite evidence transactionally in the index writer.
    """
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return _persist_exact_public_search_cover_txn(db, source)


def _persist_exact_public_search_cover_txn(
    db: Any, source: PersistedNoticeCover, *, expected_epoch: int | None = None,
) -> ConsolidatedNoticeCover:
    """Same exact-source authority inside an existing writer transaction."""
    if not db.in_transaction:
        raise ValueError("canonical_cover_transaction_required")
    edition_id, proof_id = _exact_public_search_evidence(source)
    return _persist_consolidated_cover_source_txn(
        db, canonical_edition_id=edition_id, source=source,
        relation_proof_id=proof_id, expected_epoch=expected_epoch,
    )


def _exact_public_search_evidence(source: PersistedNoticeCover) -> tuple[str, str]:
    """Derive only immutable self-identity evidence from the admitted source."""
    if (type(source) is not PersistedNoticeCover
            or source.observation.key.visibility != "public"
            or source.observation.key.branch_id != "search.public.catalogue"
            or source.observation.parser_id != "core.search.public-cover"
            or source.observation.parser_version != "2"
            or source.document.kind != "public_catalogue"
            or source.attribute.state not in ("available", "pending")
            or (source.attribute.state == "available"
                and not source.attribute.candidates)
            or (source.attribute.state == "pending"
                and source.attribute.candidates)):
        raise ValueError("unattested_exact_public_c_source")

    fields = source.observation.fields_copy().get("search_result")
    bibliography = (
        fields.get("public_bibliography") if type(fields) is dict else None
    )
    if (type(bibliography) is not dict
            or bibliography.get("source_collection") != "public_catalog"
            or bibliography.get("medium") not in (
                "digital", "physical", "unknown",
            )):
        raise ValueError("invalid_exact_public_c_bibliography")
    from .notice_search_cover_producer import _coordinate_digest
    key = source.observation.key
    if (key.record_id != "search:" + _coordinate_digest(
            key.provider_id, bibliography["source_collection"],
            bibliography.get("record_id"),
        )
            or key.edition_id != "edition:" + _coordinate_digest(
                key.provider_id, bibliography["source_collection"],
                bibliography.get("record_id"),
                bibliography.get("docbase"), bibliography["medium"],
                bibliography.get("revision_id"),
            )):
        raise ValueError("invalid_exact_public_c_coordinates")

    # The proof is bound to the immutable exact source and edition: this is
    # identity (self) evidence, not a claimed relation between two providers.
    digest = sha256(json.dumps(
        ["cifereca.notice-c.exact-public-search-source.v1",
         source.document.source_key, key.edition_id],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")).hexdigest()
    return key.edition_id, "core.exact.search.v1:" + digest


__all__ = [
    "ConsolidatedCoverSource", "ConsolidatedNoticeCover",
    "canonical_cover_document_ref", "is_canonical_cover_document",
    "read_consolidated_notice_cover", "persist_consolidated_cover_source",
    "persist_exact_public_search_cover", "persist_attested_public_cover_group",
    "persist_origin_linked_public_cover_group",
]
