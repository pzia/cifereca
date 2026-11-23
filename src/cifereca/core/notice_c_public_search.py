"""#6366/#2385: bounded SQLite-first public search over admitted Notice C facts.

Read the SAME immutable source observation that owns the N2 cover; never read
the legacy per-profile mirror as documentary authority. This bounded
cursor reads source-specific public text plus already-admitted canonical
multi-provider C relations. It does not establish a new same-edition link,
consolidate conflicting descriptions, attest full query coverage or grant rights.

No network, UI, cache mutation, retrieval of private notices, or book/image
association by title, ISBN, URL or physical-cache HIT. The caller executes
this bounded read away from the GTK main thread.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
import time
import unicodedata

from .book_cover_projection import CoverDocumentScope
from .notice_cover_projection import notice_cover_document_ref
from .notice_cover_repository import (
    _PREFIX, _FENCE_PREFIX, _decode, _check_live_source_fence, _decode_source_fence,
    _source_fingerprint,
)
from .notice_search_cover_producer import _coordinate_digest
from .notice_source_observation import NoticeSourceKey
from .public_search_projection import (
    _public_bibliographic_text, PublicSeriesRelation, decode_public_series_relation,
    PublicLanguageObservation, decode_public_language_observation,
    PublicClassification, decode_public_classification,
    _MAX_BIBLIOGRAPHY_BYTES,
)
from .search import filter_loaded_results, local_search_evidence

_MAX_SCAN = 10000
_MAX_MATCHES = 50
_STALE_AFTER = 86400
_FIELDS = frozenset(("all", "title", "author", "publisher", "subject", "series", "language"))
_SCOPES = frozenset(("all", "digital", "physical", "unknown"))
from .notice_c_public_age import PublicAgeObservation, decode_public_age_observation

_BIBLIO_FIELDS = frozenset((
    "schema", "record_id", "docbase", "scenario", "source_collection",
    "medium", "revision_id", "title", "author", "format", "publisher",
    "published_at", "description",
))
_COORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")


@dataclass(frozen=True, slots=True)
class PublicNoticeCHit:
    """Non-actionable descriptive record with its exact N2 DocumentRef."""

    document: CoverDocumentScope
    provider_id: str
    record_id: str
    docbase: str
    medium: str
    source_revision: str | None
    title: str
    author: str | None
    publisher: str | None
    media_format: str | None
    description: str | None
    observed_at: int
    last_seen_at: int
    epoch: int
    cover_state: str
    stale: bool
    edition_id: str = ""
    published_at: str | None = None
    series_relation: PublicSeriesRelation | None = None
    language_observation: PublicLanguageObservation | None = None
    classification: PublicClassification | None = None
    age_observation: PublicAgeObservation | None = None


@dataclass(frozen=True, slots=True)
class PublicNoticeCPage:
    """A page of known facts; coverage is NEVER a completed zero verdict."""

    hits: tuple[PublicNoticeCHit, ...]
    known_matches: int
    scanned_sources: int
    unindexed_sources: int
    status: str  # "partial" | "budget_exceeded"
    has_more: bool
    # In grouped mode hits contains one representative per visible C.
    # matching_sources preserves every matching, independently attested
    # contribution for those representatives. known_matches stays a source count.
    matching_sources: tuple[PublicNoticeCHit, ...] = ()
    known_documents: int | None = None


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


_NO_FENCE_CHECK = object()


def _public_observation(
    raw: object, *, key_string: object, now: int, source_proofs=None,
    fence_raw=_NO_FENCE_CHECK,
) -> PublicNoticeCHit | None:
    if type(raw) is not str or type(key_string) is not str:
        return None
    try:
        payload = json.loads(raw)
        if type(payload) is not dict:
            return None
        coords = payload.get("key")
        if type(coords) is not dict:
            return None
        key = NoticeSourceKey(**coords)
        if (key.visibility != "public"
                or key.identity_id is not None
                or key.credential_id is not None
                or key.account_id is not None
                or key.branch_id != "search.public.catalogue"
                or payload.get("parser_id") != "core.search.public-cover"
                or payload.get("parser_version") != "2"):
            return None
        document = notice_cover_document_ref(key)
        if (key_string != _PREFIX + document.source_key[len("notice-c:v1:"):]):
            return None
        persisted = _decode(raw, document)
        if fence_raw is not _NO_FENCE_CHECK:
            _check_live_source_fence(persisted, payload, _decode_source_fence(fence_raw))
        parsed = persisted.observation.fields_copy().get("search_result")
        if type(parsed) is not dict:
            return None
        bio = parsed.get("public_bibliography")
        if (type(bio) is not dict or not _BIBLIO_FIELDS <= set(bio)
                or set(bio) - _BIBLIO_FIELDS - {"series_relation", "language_observation", "classification", "age_observation"}
                or type(bio["schema"]) is not int or bio["schema"] != 1
                or bio["source_collection"] != "public_catalog"
                or bio["medium"] not in ("digital", "physical", "unknown")):
            return None

        def coord(name: str, optional: bool = False) -> bool:
            value = bio[name]
            return (value is None and optional) or (
                type(value) is str and _COORD.fullmatch(value) is not None
                and "://" not in value and ".." not in value
            )

        if (not all(coord(name) for name in (
                    "record_id", "docbase", "scenario",
                ))
                or not coord("revision_id", optional=True)
                or key.record_id != "search:" + _coordinate_digest(
                    key.provider_id, bio["source_collection"], bio["record_id"],
                )
                or key.edition_id != "edition:" + _coordinate_digest(
                    key.provider_id, bio["source_collection"], bio["record_id"],
                    bio["docbase"], bio["medium"], bio["revision_id"],
                )):
            return None
        if (not _public_bibliographic_text(
                    bio["title"], required=True, limit=512,
            ) or bio["title"].lstrip().casefold().startswith(
                ("/", "\\", "~/", "file:")
            )):
            return None
        for name in ("author", "format", "publisher", "published_at", "description"):
            value = bio[name]
            if (value is not None
                    and (not _public_bibliographic_text(
                        value, limit=4000 if name == "description" else 512,
                    ) or value.lstrip().casefold().startswith(("/", "\\", "~/", "file:")))):
                return None
        relation = None
        if "series_relation" in bio:
            relation = decode_public_series_relation(
                bio["series_relation"], provider_id=key.provider_id,
                scenario=bio["scenario"],
            )
            if relation is None:
                return None
        language = None
        if "language_observation" in bio:
            language = decode_public_language_observation(
                bio["language_observation"], provider_id=key.provider_id,
                scenario=bio["scenario"],
            )
            if language is None:
                return None
        classification = None
        if "classification" in bio:
            classification = decode_public_classification(bio["classification"])
            if classification is None:
                return None
        age = None
        if "age_observation" in bio:
            age = decode_public_age_observation(
                bio["age_observation"], provider_id=key.provider_id,
                record_id=bio["record_id"], docbase=bio["docbase"],
                medium=bio["medium"], source_revision=bio["revision_id"],
            )
            if age is None:
                return None
        last_seen = payload.get("last_seen_at", persisted.observation.observed_at)
        if (type(last_seen) is not int
                or last_seen < persisted.observation.observed_at
                or last_seen > 2**63 - 1):
            return None
        if source_proofs is not None:
            # Internal read-snapshot proof, not a new public hit field.
            source_proofs[document.source_key] = (
                persisted.observation.source_revision, persisted.attribute.state,
                tuple(sorted({x.url for x in persisted.attribute.candidates}))
                if persisted.attribute.state == "available" else (),
            )
        return PublicNoticeCHit(
            document=document, provider_id=key.provider_id,
            record_id=bio["record_id"], docbase=bio["docbase"],
            medium=bio["medium"], source_revision=bio["revision_id"],
            title=bio["title"], author=bio["author"],
            publisher=bio["publisher"], media_format=bio["format"],
            description=bio["description"],
            observed_at=persisted.observation.observed_at,
            last_seen_at=last_seen, epoch=persisted.epoch,
            cover_state=persisted.attribute.state,
            stale=now - last_seen > _STALE_AFTER,
            edition_id=key.edition_id,
            published_at=bio["published_at"], series_relation=relation,
            language_observation=language,
            classification=classification, age_observation=age,
        )
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError,
            UnicodeError, RecursionError):
        return None


def _public_text_score(query, field, title, author, publisher, description,
                       classification=None, series=None, language=None):
    """Same local relevance for C fields and source labels, without joins."""
    texts = (title, author, publisher, description)
    # SQLite prefilter may see corrupt unadmitted JSON. Bound text before
    # scoring and never stringify non-text payloads.
    if any(value is not None and (type(value) is not str or len(value) > limit)
           for value, limit in zip(texts, (512, 512, 512, 4000))):
        return 0
    metadata = classification.wire() if classification is not None else {}
    if series is not None:
        metadata["series"] = [series.label]
    if language is not None:
        metadata["languages"] = list(language.languages)
    if field == "all":
        score, _ = local_search_evidence({
            "title": title, "author": author, "publisher": publisher,
            "description": description,
            "metadata": metadata,
        }, query)
        return score
    if field in {"subject", "series", "language"}:
        if {"subject": classification, "series": series, "language": language}[field] is None:
            return 0
        return int(bool(filter_loaded_results(
            [{"source": "notice_c_public", "metadata": metadata}], query, field=field,
        )))
    selected = {"title": title, "author": author, "publisher": publisher}[field]
    return int(bool(selected) and _fold(query) in _fold(selected))


def _hit_score(record, *, field, query):
    return _public_text_score(query, field, record.title, record.author,
                              record.publisher, record.description, record.classification,
                              record.series_relation, record.language_observation)


def _matches_c_format(medium, media_format, content_types):
    if not content_types:
        return True
    # Same descriptive row and format policy as the existing Search UI.
    # The SQL prefilter may see corrupt raw JSON; do not coerce it.
    if (type(medium) is not str or medium not in ("physical", "digital", "unknown")
            or (media_format is not None
                and (type(media_format) is not str or len(media_format) > 512))):
        return False
    return bool(filter_loaded_results(
        [{"medium": medium, "media_format": media_format}],
        content_types=content_types,
    ))


def _matches_c_age(observation, age, age_kind):
    if age is None:
        return True
    if observation is None:
        return False
    from .attested_age_filter import attested_age_status
    return attested_age_status({
        'provider_id': observation.provider_id, 'item_id': observation.record_id,
        'metadata': {'youth_age_ranges': observation.filter_ranges()},
    }, age, age_kind=age_kind) == 'match'


def _matches_public_c(
    record: PublicNoticeCHit, *, wanted: set[str],
    scope: str, field: str, needle: str, content_types=(), age=None, age_kind=None,
) -> bool:
    if wanted and record.provider_id not in wanted:
        return False
    if scope != "all" and record.medium != scope:
        return False
    return (_matches_c_age(record.age_observation, age, age_kind)
            and _matches_c_format(record.medium, record.media_format, content_types)
            and _hit_score(record, field=field, query=needle) > 0)


def _canonical_candidate_refs(db, *, prefix, wanted, scope, field, needle, limit,
                              content_types=(), age=None, age_kind=None):
    """Prefilter with the very same matcher as the admitted source cursor.

    It is not a physical full-text index. All competing source claims still
    need strict decoding and an exact-current observation check.
    """
    raw = "CASE WHEN src.type='object' THEN src.value ELSE '{}' END"
    columns = ", ".join(
        f"json_extract({raw}, '$.public_bibliography.{name}')"
        for name in ("title", "author", "publisher", "description",
                     "medium", "media_format", "classification", "series_relation",
                     "language_observation", "age_observation", "record_id", "docbase", "source_revision")
    ) + f", json_extract({raw}, '$.provider')"
    def score(title, author, publisher, description, medium, media_format,
              raw_classification, raw_series, raw_language, raw_age, record_id,
              docbase, revision, provider):
        if age is not None:
            if type(raw_age) is not str or len(raw_age) > _MAX_BIBLIOGRAPHY_BYTES:
                return 0
            try:
                observation = decode_public_age_observation(
                    json.loads(raw_age), provider_id=provider, record_id=record_id,
                    docbase=docbase, medium=medium, source_revision=revision,
                )
            except (ValueError, TypeError, RecursionError):
                return 0
            if not _matches_c_age(observation, age, age_kind):
                return 0
        if not _matches_c_format(medium, media_format, content_types):
            return 0
        classification = None
        if raw_classification is not None:
            if (type(raw_classification) is not str
                    or len(raw_classification) > _MAX_BIBLIOGRAPHY_BYTES):
                return 0
            try:
                classification = decode_public_classification(json.loads(raw_classification))
            except (ValueError, TypeError, RecursionError):
                return 0
            if classification is None:
                return 0
        series = language = None
        for value, decoder in ((raw_series, decode_public_series_relation),
                               (raw_language, decode_public_language_observation)):
            if value is None:
                continue
            if type(value) is not str or len(value) > _MAX_BIBLIOGRAPHY_BYTES:
                return 0
            try:
                fact = decoder(json.loads(value), provider_id=provider)
            except (ValueError, TypeError, RecursionError):
                return 0
            if fact is None:
                return 0
            if decoder is decode_public_series_relation:
                series = fact
            else:
                language = fact
        return _public_text_score(needle, field, title, author, publisher, description,
                                  classification, series, language)
    db.create_function("cifereca_c_search_match", 14, score, deterministic=True)
    try:
        rows = db.execute(
            "SELECT DISTINCT src.key FROM metadata AS m, "
            "json_each(CASE WHEN json_valid(m.value) THEN m.value ELSE '{}' END, "
            "'$.sources') AS src "
            "WHERE m.key>=? AND m.key<? AND typeof(src.key)='text' "
            f"AND json_extract({raw}, '$.visibility')='public' "
            f"AND (?='all' OR json_extract({raw}, '$.public_bibliography.medium')=?) "
            f"AND (?=0 OR json_extract({raw}, '$.provider') "
            "IN (SELECT value FROM json_each(?))) "
            f"AND cifereca_c_search_match({columns}) > 0 "
            "ORDER BY src.key LIMIT ?",
            (prefix, prefix + "\uffff", scope, scope, len(wanted),
             json.dumps(sorted(wanted)), limit + 1),
        ).fetchall()
    finally:
        db.create_function("cifereca_c_search_match", 14, None)
    return {row[0] for row in rows[:limit]}, len(rows) > limit


def search_public_notice_c(
    storage: object, query: str, *, field: str = "all",
    scope: str = "all", provider_ids: tuple[str, ...] = (),
    page: int = 0, page_size: int = _MAX_MATCHES,
    max_scan: int = _MAX_SCAN, now: int | None = None,
    group_by_document: bool = False, content_types: tuple[str, ...] = (),
    age: int | None = None, age_kind: str | None = None,
) -> PublicNoticeCPage:
    """Search durable public C source rows without a supplier or account GET.

    This cursor bounds transient observations and durable candidate sources
    independently by max_scan; exact competing claims are always examined. It never
    masquerades a partial page as a complete supplier inventory and returns
    no cached entitlement. No per-viewer key: 3 reader profiles see the same
    admitted public C. Private observations are discarded before projection.
    """
    # Refuse foreign request values BEFORE frozenset membership or SQL
    # preparation. A hostile __hash__/__eq__ on field/scope is not a safe
    # unsupported filter; it must not execute on a Core search worker.
    if (type(query) is not str or not 2 <= len(query.strip()) <= 160
            # An invisible bidi/format control cannot become a silent
            # partial or zero-result documentary query in a shared C cursor.
            or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in query)
            or type(field) is not str or field not in _FIELDS
            or type(scope) is not str or scope not in _SCOPES
            or type(provider_ids) is not tuple or len(provider_ids) > 128
            or any(type(provider) is not str or not 1 <= len(provider) <= 64
                   or provider != provider.strip()
                   for provider in provider_ids)
            or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
                   for provider in provider_ids for char in provider)
            or type(content_types) is not tuple or len(content_types) > 32
            or any(type(value) is not str or not 0 < len(value) <= 64
                   or value != value.strip()
                   for value in content_types)
            or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
                   for value in content_types for char in value)
            or (age is not None and (type(age) is not int or not 0 <= age < 18))
            or (age is None and age_kind is not None)
            or (age_kind is not None and (type(age_kind) is not str or age_kind not in
                ('interest', 'reading', 'publisher', 'recommended')))
            or type(group_by_document) is not bool
            or type(page) is not int or not 0 <= page <= 200
            or type(page_size) is not int or not 1 <= page_size <= _MAX_MATCHES
            or type(max_scan) is not int or not 1 <= max_scan <= 30000
            or (now is not None and (type(now) is not int
                                     or not 1 <= now <= 2**63 - 1))):
        raise ValueError("invalid_notice_c_search_request")
    now = int(time.time()) if now is None else now
    needle = query.strip()
    wanted = set(provider_ids)
    matches: list[PublicNoticeCHit] = []
    # Presence, not successful decoding, suppresses retained older facts.
    # These opaque keys do not expose any private row or invalid payload.
    present_source_refs: set[str] = set()
    source_proofs = {}
    # Exact source ref -> unique admitted canonical index relation. This is
    # read-only, identity-scoped by the source itself, never viewer/profile.
    canonical_routes: dict[str, tuple[object, int, object, str]] = {}
    scanned = unindexed = 0
    exceeded = False
    # SQLite's metadata PK is indexed; the transient range is bounded.
    # The durable query uses a text prefilter, not the legacy profile mirror.
    with storage._lock, storage._connect() as db:
        # The metadata reads must share a SQLite snapshot, including when
        # a separate Storage instance updates a source concurrently.
        db.execute("BEGIN")
        cursor = db.execute(
            "SELECT m.key,m.value,f.value FROM metadata AS m "
            "LEFT JOIN metadata AS f ON f.key=? || substr(m.key,?) "
            "WHERE m.key>=? AND m.key<? "
            "AND CASE WHEN json_valid(m.value) THEN "
            "json_extract(m.value, '$.key.visibility') ELSE NULL END='public' "
            "ORDER BY m.key LIMIT ?",
            (_FENCE_PREFIX, len(_PREFIX) + 1,
             _PREFIX, _PREFIX + "\uffff", max_scan + 1),
        )
        for key_string, raw, fence_raw in cursor:
            if scanned >= max_scan:
                exceeded = True
                break
            scanned += 1
            present_source_refs.add('notice-c:v1:' + key_string[len(_PREFIX):])
            record = _public_observation(raw, key_string=key_string, now=now,
                                         source_proofs=source_proofs, fence_raw=fence_raw)
            if record is None:
                # Older cover-only sources are expected; no false 'missing'.
                unindexed += 1
                continue
            if _matches_public_c(
                record, wanted=wanted, scope=scope,
                field=field, needle=needle, content_types=content_types,
                age=age, age_kind=age_kind,
            ):
                matches.append(record)
        cursor.close()  # Release the limited statement before registering a UDF.

        # Durable C and transient source observations have independent
        # read budgets. A large transient inventory must never hide a small
        # matching retained C, or prevent exact N2 routing of live matches.
        from .notice_c_cover_index import (
            _PREFIX as _CANON_PREFIX, _json as _canon_json,
            _decode as _decode_canonical, canonical_cover_document_ref,
        )
        refs, canonical_exceeded = _canonical_candidate_refs(
            db, prefix=_CANON_PREFIX, wanted=wanted, scope=scope, field=field,
            needle=needle, limit=max_scan, content_types=content_types,
            age=age, age_kind=age_kind,
        )
        exceeded = exceeded or canonical_exceeded
        refs.update(hit.document.source_key for hit in matches)
        # Discover every competing canonical claim for each candidate, even
        # those whose retained text/provider filter did not match this query.
        # A text prefilter must NEVER select a stronger relation by omission.
        staged = {}
        if refs:
            cursor = db.execute(
                "SELECT m.key,m.value FROM metadata AS m "
                "WHERE m.key>=? AND m.key<? AND EXISTS ("
                "SELECT 1 FROM json_each(CASE WHEN json_valid(m.value) "
                "THEN m.value ELSE '{}' END, '$.sources') AS src "
                "WHERE src.key IN (SELECT value FROM json_each(?))) ORDER BY m.key",
                (_CANON_PREFIX, _CANON_PREFIX + "\uffff", json.dumps(sorted(refs))),
            )
            for key_string, raw in cursor:
                try:
                    row = _canon_json(raw)
                    ref = canonical_cover_document_ref(row["edition_id"])
                    if key_string != _CANON_PREFIX + ref.source_key.split(":")[-1]:
                        continue
                    consolidated = _decode_canonical(raw, ref)
                    public_sources = tuple(
                        src for src in consolidated.sources
                        if src.visibility == "public" and src.public_bibliography is not None
                    )
                    # The public Search cursor and the exact-source
                    # resolver must agree: a relation with an unqualified
                    # or private member is not a public group. Keep live
                    # independent sources searchable, but never route them
                    # through that mixed authority.
                    if len(public_sources) != len(consolidated.sources):
                        continue
                    for source in public_sources:
                        if source.source_ref not in refs:
                            continue
                        # Keep only the information needed to distinguish
                        # an exact self C, one foreign relation, or conflict.
                        # Thousands of duplicate claims cannot fill memory.
                        own_ref = canonical_cover_document_ref(
                            source.public_bibliography.edition_id,
                        )
                        options = staged.setdefault(source.source_ref, [])
                        if (ref == own_ref
                                or sum(item[0] != own_ref for item in options) < 2):
                            options.append((ref, consolidated, source,
                                            len(public_sources)))
                except (TypeError, ValueError, KeyError, AttributeError):
                    unindexed += 1
                    continue

        # A live observation outside the initial source budget still wins
        # over its retained older bibliography. Look it up by the *exact*
        # opaque source key inside the same read; no text-based join.
        absent_fences = {}
        remaining = sorted(set(staged) - present_source_refs)
        if remaining:
            keys = [_PREFIX + ref[len("notice-c:v1:"):] for ref in remaining
                    if ref.startswith("notice-c:v1:")]
            cursor = db.execute(
                "SELECT requested.value,m.key,m.value,f.value FROM json_each(?) AS requested "
                "LEFT JOIN metadata AS m ON m.key=requested.value "
                "LEFT JOIN metadata AS f ON f.key=? || substr(requested.value,?)",
                (json.dumps(keys), _FENCE_PREFIX, len(_PREFIX) + 1),
            )
            for requested_key, key_string, raw, fence_raw in cursor:
                if key_string is None:
                    absent_fences['notice-c:v1:' + requested_key[len(_PREFIX):]] = fence_raw
                    continue
                present_source_refs.add('notice-c:v1:' + key_string[len(_PREFIX):])
                current = _public_observation(raw, key_string=key_string, now=now,
                                              source_proofs=source_proofs, fence_raw=fence_raw)
                if current is None:
                    unindexed += 1
                    continue
                if _matches_public_c(current, wanted=wanted, scope=scope,
                                     field=field, needle=needle,
                                     content_types=content_types,
                                     age=age, age_kind=age_kind):
                    matches.append(current)

        for source_ref, candidates in staged.items():
            own = [
                item for item in candidates
                if item[0] == canonical_cover_document_ref(
                    item[2].public_bibliography.edition_id
                )
            ]
            foreign = [
                item for item in candidates
                if item[0] != canonical_cover_document_ref(
                    item[2].public_bibliography.edition_id
                )
            ]
            if len(foreign) == 1 and foreign[0][3] >= 2:
                selected_route = foreign[0]
            elif len(own) == 1:
                # No foreign relation, or contradictory claims: the
                # source's exact independent canonical ref wins.
                selected_route = own[0]
            elif len(foreign) == 1 and not own:
                # Only an explicit multi-public source group may
                # rebind an unrelated provider's edition.
                selected_route = (
                    foreign[0] if foreign[0][3] >= 2 else None
                )
            else:
                selected_route = None
            if selected_route is None:
                continue
            ref, consolidated, source, _strength = selected_route
            bib = source.public_bibliography
            # The index decoder rederives the exact source ref and
            # edition from the original provider coordinates.
            aggregate_cover = (
                "available" if consolidated.candidates
                else "missing" if consolidated.snapshot_complete
                else "pending"
            )
            canonical_routes[source_ref] = (
                ref, consolidated.epoch, source, aggregate_cover,
            )
            if source_ref in present_source_refs:
                # A live row, even rejected, is not retention eviction.
                # Never substitute older canonical text for it. Rebind
                # admitted live N2 only if its exact proof still matches.
                continue
            # Keep the retained C bibliography searchable even after a
            # legacy/unqualified source loss, but do not portray its age or
            # cover as current unless a Core-produced eviction fence attests
            # the exact next epoch. An incompatible fence means the retained
            # source was superseded, not merely evicted.
            try:
                fence = _decode_source_fence(absent_fences.get(source_ref))
                if fence is not None and (
                    fence['epoch'] != source.source_epoch + int(fence.get('evicted') is True)
                    or fence['proof'] not in {
                        _source_fingerprint(source, ('search_result', 'thumbnail_url'), complete)
                        for complete in (True, False)
                    }
                ):
                    unindexed += 1
                    continue
                unqualified_retention = fence is None or fence['evicted'] is not True
            except ValueError:
                unindexed += 1
                continue
            hit = PublicNoticeCHit(
                document=ref, provider_id=source.provider_id,
                record_id=bib.record_id, docbase=bib.docbase,
                medium=bib.medium,
                source_revision=bib.source_revision,
                title=bib.title, author=bib.author,
                publisher=bib.publisher,
                media_format=bib.media_format,
                description=bib.description,
                observed_at=source.observed_at,
                last_seen_at=bib.last_seen_at,
                epoch=consolidated.epoch,
                cover_state="pending" if unqualified_retention else aggregate_cover,
                stale=unqualified_retention or now - bib.last_seen_at > _STALE_AFTER,
                edition_id=bib.edition_id,
                published_at=bib.published_at, series_relation=bib.series_relation,
                language_observation=bib.language_observation,
                classification=bib.classification, age_observation=bib.age_observation,
            )
            if _matches_public_c(
                hit, wanted=wanted, scope=scope,
                field=field, needle=needle, content_types=content_types,
                age=age, age_kind=age_kind,
            ):
                matches.append(hit)

    # Pagination is over *known* source facts, not a confirmed total.
    # Rank by the exact source edition, not the current storage/N2
    # document ref: after observation eviction the latter may become a
    # canonical ref shared by several independently matching sources.
    def source_order(hit):
        return (
            -_hit_score(hit, field=field, query=needle),
            _fold(hit.title), hit.provider_id, hit.edition_id,
            hit.record_id, hit.docbase, hit.medium, hit.source_revision or "",
            hit.document.source_key,
        )

    matches.sort(key=source_order)
    offset = page * page_size
    # Resolve the exact document before choosing a page. In grouped mode
    # a C is the unit of pagination, not its individual provider facts.
    selected = matches if group_by_document else matches[offset:offset + page_size]
    if selected and canonical_routes:
        # Live source hits keep their own text and current source revision.
        # A canonical index may change only the N2 ref after verifying the
        # precise retained provider/edition/source epoch and public fields.
        canonical_hits = []
        for hit in selected:
            route = canonical_routes.get(hit.document.source_key)
            if route is not None:
                ref, epoch, src, cover_state = route
                bib = src.public_bibliography
                if (source_proofs.get(hit.document.source_key) == (
                        src.source_revision, src.state, tuple(sorted(set(src.urls))),
                    )
                        and src.source_epoch == hit.epoch
                        and src.provider_id == hit.provider_id
                        and src.visibility == "public"
                        and bib is not None
                        and bib.edition_id == hit.edition_id
                        and bib.record_id == hit.record_id
                        and bib.docbase == hit.docbase
                        and bib.medium == hit.medium
                        and bib.source_revision == hit.source_revision
                        and bib.title == hit.title
                        and bib.author == hit.author
                        and bib.publisher == hit.publisher
                        and bib.media_format == hit.media_format
                        and bib.description == hit.description
                        and bib.published_at == hit.published_at
                        and src.observed_at == hit.observed_at
                        and bib.series_relation == hit.series_relation
                        and bib.language_observation == hit.language_observation
                        and bib.classification == hit.classification
                        and bib.age_observation == hit.age_observation):
                    hit = replace(
                        hit, document=ref, epoch=epoch,
                        cover_state=cover_state,
                    )
            canonical_hits.append(hit)
        selected = canonical_hits
    matching_sources = ()
    known_documents = None
    if group_by_document:
        # Use a stable ordering of matching source facts, including edition
        # coordinates as the tie-breaker independent of source TTL and N2.
        selected.sort(key=source_order)
        groups: dict[CoverDocumentScope, list[PublicNoticeCHit]] = {}
        for hit in selected:
            groups.setdefault(hit.document, []).append(hit)
        known_documents = len(groups)
        page_groups = list(groups.values())[offset:offset + page_size]
        selected = [group[0] for group in page_groups]
        matching_sources = tuple(hit for group in page_groups for hit in group)
    return PublicNoticeCPage(
        hits=tuple(selected),
        known_matches=len(matches), scanned_sources=scanned,
        unindexed_sources=unindexed,
        status="budget_exceeded" if exceeded else "partial",
        has_more=exceeded or offset + page_size < (
            known_documents if known_documents is not None else len(matches)
        ),
        matching_sources=matching_sources, known_documents=known_documents,
    )


__all__ = [
    "PublicNoticeCHit", "PublicNoticeCPage", "search_public_notice_c",
]
