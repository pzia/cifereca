"""#6366: minimal REAL same-edition authority from completed Core OriginLinks.

A title, ISBN, image URL, content SHA, catalogue metadata or a claim from a
provider are never sufficient to create a shared Notice C. The independent
Core v14 publication graph has multiple *completed provider* OriginLinks, each
joined to its own positive provider-response SourceReference and to the SAME
present Artifact Placement under the SAME identity. Only that exact
multi-record, one-artifact proof can attach independently witnessed
anonymous public Search source editions to a shared cover C.

This worker-only producer performs no provider dispatch, N3 read or account
action. It emits ONLY public, provider-attested bibliographic/cover facts.
Private account, profile, delivery receipt, file path and right never enter C.
"""
from __future__ import annotations

import json
import sqlite3

from .notice_c_cover_index import (
    persist_origin_linked_public_cover_group, _MAX_SOURCES,
)
from .notice_cover_repository import (
    PersistedNoticeCover, _PREFIX as _SOURCE_PREFIX, _decode as _decode_source,
)
from .notice_c_public_search import _public_observation, _fold

_MAX_ORIGINS = 100000


def _public_source_coordinates(
    source: PersistedNoticeCover,
) -> tuple[str, str] | None:
    """Only the immutable v2 same-call public source; no profile or rights."""
    if (type(source) is not PersistedNoticeCover
            or source.observation.key.visibility != "public"
            or source.observation.key.branch_id != "search.public.catalogue"
            or source.observation.parser_id != "core.search.public-cover"
            or source.observation.parser_version != "2"
            or source.document.kind != "public_catalogue"
            or source.attribute.state not in ("available", "pending")):
        return None
    fields = source.observation.fields_copy().get("search_result")
    bibliography = (
        fields.get("public_bibliography") if type(fields) is dict else None
    )
    if (type(bibliography) is not dict
            or bibliography.get("source_collection") != "public_catalog"
            or bibliography.get("medium") != "digital"
            or type(bibliography.get("record_id")) is not str):
        return None
    from .notice_search_cover_producer import _coordinate_digest
    key = source.observation.key
    record = bibliography["record_id"]
    if (not record
            or key.record_id != "search:" + _coordinate_digest(
                key.provider_id, "public_catalog", record,
            )
            or key.edition_id != "edition:" + _coordinate_digest(
                key.provider_id, "public_catalog", record,
                bibliography.get("docbase"), "digital",
                bibliography.get("revision_id"),
            )):
        return None
    return key.provider_id, record


def _admitted_public_sources_txn(
    db, wanted: set[tuple[str, str]],
) -> dict[tuple[str, str], PersistedNoticeCover]:
    """Read only independently persisted source records relevant to origins."""
    if not wanted:
        return {}
    from .notice_search_cover_producer import _coordinate_digest

    # The source record hash intentionally excludes docbase/revision: every
    # competing edition of the same provider record must remain visible to
    # the collision veto. The full decoder still proves the exact source.
    keys = sorted({
        (provider, "search:" + _coordinate_digest(provider, "public_catalog", record))
        for provider, record in wanted
    })
    admitted: dict[tuple[str, str], PersistedNoticeCover] = {}
    collisions: set[tuple[str, str]] = set()
    cursor = db.execute(
        "SELECT key,value FROM metadata WHERE key>=? AND key<? "
        "AND json_extract(CASE WHEN json_valid(value) THEN value "
        "ELSE '{}' END, '$.key.visibility')='public' "
        "AND json_array(json_extract(CASE WHEN json_valid(value) THEN value "
        "ELSE '{}' END, '$.key.provider_id'), "
        "json_extract(CASE WHEN json_valid(value) THEN value ELSE '{}' END, "
        "'$.key.record_id')) IN (SELECT json(value) FROM json_each(?)) "
        "ORDER BY key",
        (_SOURCE_PREFIX, _SOURCE_PREFIX + "\uffff", json.dumps(keys)),
    )
    for key_string, raw in cursor:
        hit = _public_observation(
            raw, key_string=key_string, now=1,
        )
        if hit is None or hit.medium != "digital":
            continue
        # Reuse the already fetched indexed source payload; one bounded
        # SQLite cursor instead of one extra connection per public result.
        try:
            source = _decode_source(raw, hit.document)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            continue
        coords = _public_source_coordinates(source)
        if coords is None:
            continue
        if coords in admitted:
            # Multiple public revisions/editions per provider item are
            # ambiguous. Do not pick a most-recent or first-listed variant.
            collisions.add(coords)
        else:
            admitted[coords] = source
    for coords in collisions:
        admitted.pop(coords, None)
    if admitted:
        # Eviction of a short-lived source is not evidence that another
        # edition ceased to exist. Retained C provides only a collision
        # veto here; it never supplies a missing positive source input.
        from .notice_c_cover_index import (
            _PREFIX, _json, _decode, _db_key, canonical_cover_document_ref,
        )
        retained = db.execute(
            "SELECT m.key,m.value FROM metadata AS m "
            "WHERE m.key>=? AND m.key<? AND EXISTS ("
            "SELECT 1 FROM json_each(CASE WHEN json_valid(m.value) "
            "THEN m.value ELSE '{}' END, '$.sources') AS src WHERE "
            "json_array(json_extract(CASE WHEN src.type='object' "
            "THEN src.value ELSE '{}' END, '$.provider'),"
            "json_extract(CASE WHEN src.type='object' "
            "THEN src.value ELSE '{}' END, '$.public_bibliography.record_id')) "
            "IN (SELECT json(value) FROM json_each(?))) ORDER BY m.key",
            (_PREFIX, _PREFIX + "\uffff", json.dumps(sorted(admitted))),
        )
        for key_string, raw in retained:
            claimed = set()
            try:
                # The SQL prefilter selects claims by provider/record.
                # Retain those coordinates for a local veto even if the
                # canonical row fails its authoritative decoder.
                wire = json.loads(raw)
                members = wire.get("sources") if type(wire) is dict else None
                if type(members) is dict:
                    for member in members.values():
                        if type(member) is not dict:
                            continue
                        bio = member.get("public_bibliography")
                        provider = member.get("provider")
                        record = bio.get("record_id") if type(bio) is dict else None
                        if type(provider) is str and type(record) is str:
                            coords = (provider, record)
                            if coords in admitted:
                                claimed.add(coords)
                if not claimed:
                    continue
                document = canonical_cover_document_ref(_json(raw)["edition_id"])
                if _db_key(document) != key_string:
                    raise ValueError("corrupt_canonical_cover")
                consolidated = _decode(raw, document)
            except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                for coords in claimed:
                    admitted.pop(coords, None)
                continue
            for member in consolidated.sources:
                bio = member.public_bibliography
                if member.visibility != "public" or bio is None or bio.medium != "digital":
                    continue
                coords = (member.provider_id, bio.record_id)
                current = admitted.get(coords)
                if current is not None and bio.edition_id != current.observation.key.edition_id:
                    admitted.pop(coords)
            if not admitted:
                break
    return admitted



def _admitted_public_sources(storage, wanted):
    with storage._lock, storage._connect() as db:
        return _admitted_public_sources_txn(db, wanted)

# A single private Core authority token, never serialized, returned or exposed
# to a provider. Live UI can validate this after the worker has persisted
# ALL exact-source contributions to one cross-provider C.
_CORE_ORIGIN_LINK_C_WITNESS = object()


def _eligible_origin_groups(verified, public, current_by_coords):
    """Share the exact live-Search relation rule with transactional replay.

    All inputs belong to the caller's snapshot. Text is solely a veto.
    No rights or private provenance enter the yielded public sources.
    """
    groups: dict[tuple[str, str], set[tuple[str, str]]] = {}
    membership: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for artifact, owner, provider, _credential, _account, edition, kind in verified:
        # Keep the first P0 proof to realized purchases; loans can expire
        # independently and cannot currently anchor a durable public join.
        if kind != "purchase":
            continue
        coords = (provider, edition)
        if coords not in public:
            continue
        group = (artifact, owner)
        groups.setdefault(group, set()).add(coords)
        membership.setdefault(coords, set()).add(group)

    for group, coords in groups.items():
        if (not 2 <= len(coords) <= _MAX_SOURCES
                or not (coords & current_by_coords.keys())
                or any(membership.get(coord) != {group} for coord in coords)):
            continue
        # Distinct records from one provider may have independently completed
        # origins too. Provider count is not an identity proof; the exact
        # origin membership and competing-edition veto apply to every record.
        ordered = tuple(sorted(coords))
        sources = tuple(public[coord] for coord in ordered)
        refs = {source.document.source_key for source in sources}
        if len(refs) != len(sources):
            continue
        bibliographies = [
            source.observation.fields_copy()["search_result"]["public_bibliography"]
            for source in sources
        ]
        # Same bytes + completed receipts are not sufficient when the
        # catalogues contradict each other. Text only vetoes a strong link;
        # it never creates one. Check every pair, including later members.
        if (len({_fold(bio["title"]) for bio in bibliographies}) != 1
                or len({_fold(bio["author"]) for bio in bibliographies
                        if bio.get("author")}) > 1):
            continue
        yield ordered, sources


def _purchase_coordinates(verified):
    return {(provider, edition)
            for _artifact, _owner, provider, _credential, _account, edition, kind in verified
            if kind == "purchase"}


def reattest_exact_origin_group_txn(db, sources):
    """Revalidate an existing public group with the same production rule.

    A changed membership must go through relink, not recompute. A retained C
    or a cached proof ID alone is never sufficient to reattest a relation.
    The caller owns BEGIN IMMEDIATE and the canonical publication transaction.
    """
    if not db.in_transaction:
        raise ValueError("notice_origin_transaction_required")
    if type(sources) is not tuple or not 2 <= len(sources) <= _MAX_SOURCES:
        raise ValueError("notice_origin_group_invalid")
    currents = {}
    for source in sources:
        coords = _public_source_coordinates(source)
        if coords is None or coords in currents:
            raise ValueError("notice_origin_group_invalid")
        currents[coords] = source

    from .library_projection import _read_provider_origin_coordinates
    verified = _read_provider_origin_coordinates(
        db, identity_id=None, with_kind=True,
    )
    if not verified or len(verified) > _MAX_ORIGINS:
        raise ValueError("notice_origin_relation_changed")
    wanted = _purchase_coordinates(verified)
    if not currents.keys() <= wanted:
        raise ValueError("notice_origin_relation_changed")
    public = _admitted_public_sources_txn(db, wanted)
    if any(public.get(coord) != source for coord, source in currents.items()):
        raise ValueError("notice_origin_relation_changed")
    matches = [
        group for ordered, group in _eligible_origin_groups(verified, public, currents)
        if set(ordered) == currents.keys()
    ]
    if len(matches) != 1:
        raise ValueError("notice_origin_relation_changed")
    return matches[0]


def reconcile_origin_linked_public_batch(
    storage, currents: list[PersistedNoticeCover], *, failed_sources=None,
) -> dict[str, object]:
    """Reconcile one bounded live Search batch using one OriginLink snapshot.

    Return exact source DocumentRef keys -> linked canonical C, only for
    currently admitted public v2 source observations in this batch.
    """
    if type(currents) is not list or not 1 <= len(currents) <= 256:
        return {}
    current_by_coords = {}
    for current in currents:
        coords = _public_source_coordinates(current)
        if coords is not None and coords not in current_by_coords:
            current_by_coords[coords] = current
    if not current_by_coords:
        return {}

    # Core's established read-only OriginLink projection checks complete
    # provider-response SourceReferences, delivery receipt, positive
    # provider provenance, present same-owner Placement and valid Artifact.
    from .library_projection import _verified_provider_origin_coordinates
    verified = _verified_provider_origin_coordinates(
        storage, identity_id=None, with_kind=True,
    )
    if not verified or len(verified) > _MAX_ORIGINS:
        return {}
    # Only purchases already attested by the Core publication graph may
    # participate in reconciliation. Unrelated source rows cannot exhaust
    # a global scan budget or obscure a competing revision.
    wanted = _purchase_coordinates(verified)
    if not (wanted & current_by_coords.keys()):
        return {}
    public = _admitted_public_sources(storage, wanted)
    if not public:
        return {}
    current_by_coords = {
        coords: source for coords, source in current_by_coords.items()
        if public.get(coords) == source
    }
    if not current_by_coords:
        return {}

    outcomes: dict[str, object] = {}
    for ordered, sources in _eligible_origin_groups(
        verified, public, current_by_coords,
    ):
        refs = {source.document.source_key for source in sources}
        try:
            complete = persist_origin_linked_public_cover_group(
                storage, sources=sources,
            )
            if (len(complete.sources) != len(sources)
                    or {src.source_ref for src in complete.sources} != refs
                    or any(src.visibility != "public" for src in complete.sources)):
                continue
            for coord in ordered:
                if coord in current_by_coords:
                    source_ref = public[coord].document.source_key
                    outcomes[source_ref] = complete
        except (sqlite3.Error, ValueError, TypeError, OSError):
            # A concurrently replaced source or conflicted index does not
            # create a public relation or poison its own-source C.
            # The opt-in caller may retry established dirty groups only.
            # Keep the same bounded batch budget as self-C recovery.
            if failed_sources is not None:
                for coord in ordered:
                    if len(failed_sources) >= 32:
                        break
                    if coord not in current_by_coords:
                        continue
                    source = current_by_coords[coord]
                    if not any(prior.document == source.document for prior in failed_sources):
                        failed_sources.append(source)
            continue
    return outcomes


def reconcile_origin_linked_public_c(storage, current: PersistedNoticeCover):
    """Single-source adapter for use outside the batched Search ingestion."""
    if _public_source_coordinates(current) is None:
        return None
    return reconcile_origin_linked_public_batch(
        storage, [current],
    ).get(current.document.source_key)


__all__ = [
    "reconcile_origin_linked_public_batch",
    "reconcile_origin_linked_public_c",
]
