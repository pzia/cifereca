"""Worker-attested live Search -> consolidated public C cover association (#6366).

The SearchResult's own source coordinates are never a relation to another
provider. Only a *persisted* public C containing that exact source with the
current source epoch may issue a binding. The GUI checks a frozen Core packet,
not a mutable provider-supplied canonical document ref or a hot N3 URL.

Issue on the existing Search/Core worker; validate on GTK without I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import SearchResult
from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import (
    ConsolidatedNoticeCover, canonical_cover_document_ref,
    is_canonical_cover_document,
)
from .notice_cover_repository import PersistedNoticeCover, is_notice_cover_document


_WITNESS = object()
_PACKET_NAME = "_core_verified_live_multisource_c_route"


def _fields(row: SearchResult) -> tuple[object, ...]:
    return (
        row.source_provider, row.source_credential, row.source_collection,
        row.source_account_id, row.record_id, row.docbase, row.medium,
        row.revision_id, row.scenario, row.title, row.author, row.publisher,
        row.format, row.published_at, row.description, row.thumbnail_url,
        row.owned, row.downloadable, row.download_url,
    )


@dataclass(frozen=True, slots=True)
class VerifiedLiveMultisourceC:
    source_item: SearchResult = field(repr=False, compare=False)
    source_document: CoverDocumentScope
    canonical_document: CoverDocumentScope
    canonical_epoch: int
    fields: tuple[object, ...]
    witness: object = field(repr=False, compare=False)


def stamp_live_multisource_c(
    row: SearchResult,
    source: PersistedNoticeCover,
    consolidated: ConsolidatedNoticeCover,
) -> bool:
    """Issue a Core-only immutable N2 route after live source persistence.

    A reconstructed SearchResult or one with a stale/changed direct cover
    cannot claim a foreign multi-source edition. The source's *own* canonical
    C is already supported by the existing self-edition witness path.
    """
    from .search_orchestration import _verified_public_cover_witness
    from .notice_search_cover_producer import admit_public_search_catalogue
    from .notice_cover_projection import notice_cover_document_ref

    if (type(row) is not SearchResult
            or type(source) is not PersistedNoticeCover
            or type(consolidated) is not ConsolidatedNoticeCover
            or not _verified_public_cover_witness(row)
            or not is_notice_cover_document(source.document)
            or not is_canonical_cover_document(consolidated.document)
            or len(consolidated.sources) < 2
            or source.observation.key.visibility != "public"
            or source.observation.parser_version != "2"
            or consolidated.document == canonical_cover_document_ref(
                source.observation.key.edition_id,
            )
            or not all(
                s.visibility == "public" and s.public_bibliography is not None
                for s in consolidated.sources
            )):
        return False
    try:
        witness = admit_public_search_catalogue(
            row, identity_id="__core_public_view__",
            observed_at=source.observation.observed_at,
            public_cover_url=row.thumbnail_url,
        )
        if (witness is None or witness.parser_version != "2"
                or witness.key != source.observation.key
                or notice_cover_document_ref(witness.key) != source.document):
            return False
        bibliography = source.observation.fields_copy()[
            "search_result"
        ]["public_bibliography"]
        matches = [
            candidate for candidate in consolidated.sources
            if candidate.source_ref == source.document.source_key
        ]
        if len(matches) != 1:
            return False
        match = matches[0]
        facts = match.public_bibliography
        if (match.provider_id != row.source_provider
                or match.source_revision != source.observation.source_revision
                or match.source_epoch != source.epoch
                or match.observed_at != source.observation.observed_at
                or facts is None
                or facts.edition_id != source.observation.key.edition_id
                or facts.record_id != row.record_id
                or facts.docbase != row.docbase
                or facts.medium != row.medium
                or facts.source_revision != row.revision_id
                or facts.title != row.title
                or facts.author != row.author
                or facts.publisher != row.publisher
                or facts.media_format != row.format
                or facts.description != row.description
                or bibliography.get("record_id") != facts.record_id
                or (row.thumbnail_url is not None
                    and (match.state != "available"
                         or match.urls != (row.thumbnail_url,)))
                or (row.thumbnail_url is None and match.urls)):
            return False
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeError):
        return False

    packet = VerifiedLiveMultisourceC(
        source_item=row, source_document=source.document,
        canonical_document=consolidated.document,
        canonical_epoch=consolidated.epoch,
        fields=_fields(row), witness=_WITNESS,
    )
    row.__dict__[_PACKET_NAME] = packet
    row._core_notice_canonical_cover_document_ref = consolidated.document
    row._core_notice_canonical_cover_document_epoch = consolidated.epoch
    return True


def verified_live_multisource_c(
    row: SearchResult, *, source_document: CoverDocumentScope,
    canonical_document: CoverDocumentScope,
) -> bool:
    """GTK-safe and cache-free recheck of the exact worker-issued association."""
    if type(row) is not SearchResult:
        return False
    packet = getattr(row, _PACKET_NAME, None)
    return (
        type(packet) is VerifiedLiveMultisourceC
        and packet.witness is _WITNESS
        and packet.source_item is row
        and packet.source_document == source_document
        and packet.canonical_document == canonical_document
        and is_canonical_cover_document(canonical_document)
        and row.__dict__.get("_core_notice_cover_document_ref") == source_document
        and row.__dict__.get("_core_notice_canonical_cover_document_ref")
        == canonical_document
        and row.__dict__.get("_core_notice_canonical_cover_document_epoch")
        == packet.canonical_epoch
        and type(packet.canonical_epoch) is int
        and packet.canonical_epoch > 0
        and packet.fields == _fields(row)
    )


__all__ = [
    "VerifiedLiveMultisourceC", "stamp_live_multisource_c",
    "verified_live_multisource_c",
]
