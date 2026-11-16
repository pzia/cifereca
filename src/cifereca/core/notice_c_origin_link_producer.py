"""#6366: minimal REAL same-edition authority from completed Core OriginLinks.

A title, ISBN, image URL, content SHA, catalogue metadata or a claim from a
provider are never sufficient to create a shared Notice C. The independent
Core v14 publication graph has two *completed provider* OriginLinks, each
joined to its own positive provider-response SourceReference and to the SAME
present Artifact Placement under the SAME identity. Only that exact
two-provider, one-artifact proof can attach two independently witnessed
anonymous public Search source editions to a shared cover C.

This worker-only producer performs no provider dispatch, N3 read or account
action. It emits ONLY public, provider-attested bibliographic/cover facts.
Private account, profile, delivery receipt, file path and right never enter C.
"""
from __future__ import annotations

from hashlib import sha256
import json
import sqlite3

from .notice_c_cover_index import persist_consolidated_cover_source
from .notice_cover_repository import (
    PersistedNoticeCover, _PREFIX as _SOURCE_PREFIX, read_notice_cover,
)
from .notice_c_public_search import _public_observation

_MAX_PUBLIC_SOURCES = 4096
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


def _admitted_public_sources(storage) -> dict[tuple[str, str], PersistedNoticeCover] | None:
    """Bounded scan of real, separately persisted Core source observations."""
    with storage._lock, storage._connect() as db:
        rows = db.execute(
            "SELECT key,value FROM metadata WHERE key>=? AND key<? "
            "ORDER BY key LIMIT ?",
            (_SOURCE_PREFIX, _SOURCE_PREFIX + "\uffff", _MAX_PUBLIC_SOURCES + 1),
        ).fetchall()
    if len(rows) > _MAX_PUBLIC_SOURCES:
        return None
    admitted: dict[tuple[str, str], PersistedNoticeCover] = {}
    collisions: set[tuple[str, str]] = set()
    for key_string, raw in rows:
        hit = _public_observation(
            raw, key_string=key_string, now=1,
        )
        if hit is None or hit.medium != "digital":
            continue
        source = read_notice_cover(storage, hit.document)
        if source is None:
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
    return admitted


# A single private Core authority token, never serialized, returned or exposed
# to a provider. Live UI can validate this after the worker has persisted
# BOTH exact-source contributions to one cross-provider C.
_CORE_ORIGIN_LINK_C_WITNESS = object()


def reconcile_origin_linked_public_batch(
    storage, currents: list[PersistedNoticeCover],
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
    public = _admitted_public_sources(storage)
    if not public:
        return {}
    current_by_coords = {
        coords: source for coords, source in current_by_coords.items()
        if public.get(coords) == source
    }
    if not current_by_coords:
        return {}

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

    outcomes: dict[str, object] = {}
    for group, coords in groups.items():
        if (len(coords) != 2
                or not (coords & current_by_coords.keys())
                or len({provider for provider, _edition in coords}) != 2
                or any(membership.get(coord) != {group} for coord in coords)):
            continue
        pair = tuple(sorted(coords))
        first, second = (public[coord] for coord in pair)
        if first.document == second.document:
            continue

        # The stable target is derived from TWO public source edition refs,
        # never from SHA, account, receipt, title, ISBN or thumbnail.
        refs = tuple(sorted((
            first.document.source_key, second.document.source_key,
        )))
        digest = sha256(json.dumps(
            ["cifereca.c.origin-linked-public-edition.v1", *refs],
            ensure_ascii=True, separators=(",", ":"),
        ).encode("ascii")).hexdigest()
        target = "edition:origin-linked:v1:" + digest
        proof = "core.origin.linked.v1:" + digest
        try:
            persist_consolidated_cover_source(
                storage, canonical_edition_id=target, source=first,
                relation_proof_id=proof,
            )
            complete = persist_consolidated_cover_source(
                storage, canonical_edition_id=target, source=second,
                relation_proof_id=proof,
            )
            if (len(complete.sources) != 2
                    or {src.source_ref for src in complete.sources} != set(refs)
                    or any(src.visibility != "public" for src in complete.sources)):
                continue
            for coord in pair:
                if coord in current_by_coords:
                    source_ref = public[coord].document.source_key
                    outcomes[source_ref] = complete
        except (sqlite3.Error, ValueError, TypeError, OSError):
            # A concurrently replaced source or conflicted index does not
            # create a public relation or poison its own-source C.
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
