"""Private receipt of a persisted canonical C revision on the original row.

This is an ephemeral Core producer witness, not a serialized provider
attribute. A mutable SearchResult's scalar epoch alone is not its authority.
"""
from __future__ import annotations

from ..models import SearchResult
from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import ConsolidatedNoticeCover, is_canonical_cover_document

_WITNESS = object()


def _native_canonical_ref(document):
    # A frozen DocumentRef can be altered with object.__setattr__. Require
    # native scalar members before a dataclass equality comparison executes
    # on a mutable row or plug-in presentation.
    return (type(document) is CoverDocumentScope
            and all(type(getattr(document, name)) is str
                    and len(getattr(document, name)) <= 512
                    and getattr(document, name).isprintable() for name in (
                "kind", "source_key", "provider_id", "credential_id",
                "loan_id", "artifact_id", "placement_id",
            ))
            and is_canonical_cover_document(document))


def stamp_persisted_c_revision(row: SearchResult, snapshot: ConsolidatedNoticeCover) -> None:
    if (type(row) is not SearchResult
            or type(snapshot) is not ConsolidatedNoticeCover
            or not _native_canonical_ref(snapshot.document)
            or type(snapshot.epoch) is not int
            or not 1 <= snapshot.epoch < 2**63):
        raise ValueError("invalid_persisted_c_revision")
    row._core_persisted_c_revision_witness = (
        _WITNESS, id(row), snapshot.document, snapshot.epoch,
    )


def persisted_live_c_revision(row: object, document: object) -> int | None:
    if type(row) is not SearchResult or not _native_canonical_ref(document):
        return None
    proof = getattr(row, "_core_persisted_c_revision_witness", None)
    epoch = getattr(row, "_core_notice_canonical_cover_document_epoch", None)
    if (type(proof) is not tuple or len(proof) != 4
            or proof[0] is not _WITNESS or proof[1] != id(row)
            or not _native_canonical_ref(proof[2])
            or not _native_canonical_ref(
                getattr(row, "_core_notice_canonical_cover_document_ref", None))
            or proof[2] != document
            or row._core_notice_canonical_cover_document_ref != document
            or type(epoch) is not int or not 1 <= epoch < 2**63
            or proof[3] != epoch):
        return None
    return epoch
