"""#2385: six read-only catalogue presentations from the SAME admitted C page.

This is a pure UI-facing projection, not an alternate catalogue or rights
engine. Library ownership/loans must remain in their existing private model.
"""
from __future__ import annotations

from dataclasses import dataclass

from .notice_c_public_search import PublicNoticeCPage, PublicNoticeCHit
from .notice_c_public_age import PublicAgeObservation

_VIEWS = frozenset(("list", "grid", "compact", "detail", "sources", "timeline"))


@dataclass(frozen=True, slots=True)
class PublicCatalogueViewRow:
    document: object
    title: str
    subtitle: str | None
    provider_id: str
    edition_id: str
    cover_state: str
    stale: bool
    source_count: int
    observed_at: int
    last_seen_at: int
    description: str | None = None
    record_id: str = ""
    docbase: str = ""
    medium: str = ""
    source_revision: str | None = None
    age_observation: PublicAgeObservation | None = None


@dataclass(frozen=True, slots=True)
class PublicCatalogueView:
    mode: str
    rows: tuple[PublicCatalogueViewRow, ...]
    status: str
    has_more: bool
    known_matches: int
    known_documents: int | None


def project_public_catalogue_view(page: PublicNoticeCPage, *, mode: str):
    """Present six modes without inventing missing data or loan rights."""
    if type(page) is not PublicNoticeCPage or mode not in _VIEWS:
        raise ValueError("invalid_public_catalogue_view")
    counts = {}
    for hit in page.matching_sources:
        counts[hit.document] = counts.get(hit.document, 0) + 1
    rows = []
    displayed = page.matching_sources if mode == "sources" and page.matching_sources else page.hits
    for hit in displayed:
        if type(hit) is not PublicNoticeCHit:
            raise ValueError("invalid_public_catalogue_hit")
        rows.append(PublicCatalogueViewRow(
            document=hit.document, title=hit.title, subtitle=hit.author,
            provider_id=hit.provider_id, edition_id=hit.edition_id,
            cover_state=hit.cover_state, stale=hit.stale,
            source_count=counts.get(hit.document, 1),
            observed_at=hit.observed_at, last_seen_at=hit.last_seen_at,
            description=hit.description if mode == "detail" else None,
            record_id=hit.record_id, docbase=hit.docbase,
            medium=hit.medium, source_revision=hit.source_revision,
            age_observation=(hit.age_observation if mode in ('detail', 'sources') else None),
        ))
    if mode == "timeline":
        rows.sort(key=lambda row: (-row.last_seen_at, row.provider_id, row.edition_id))
    return PublicCatalogueView(
        mode=mode, rows=tuple(rows), status=page.status,
        has_more=page.has_more, known_matches=page.known_matches,
        known_documents=page.known_documents,
    )
