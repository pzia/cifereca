"""Notice C -> derived N2 cover cache adapter, no independent document truth.

Only explicitly admitted NoticeSourceObservation objects enter this adapter.
The caller decides *when* to publish (e.g. on a visible list/detail bind);
there is no GET, EPUB scan or eager catalogue traversal here.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock

from ..core.book_cover_projection import CoverDocumentScope
from ..core.notice_cover_projection import (
    NoticeCoverAttribute, project_notice_cover_attribute,
)
from ..core.notice_source_observation import NoticeSourceObservation
from .document_cover_service import CoverSourceSnapshot, DocumentCoverService


@dataclass(frozen=True, slots=True)
class _Published:
    attribute: NoticeCoverAttribute
    revision: int
    # Monotone source-observation watermark, including ignored partial reads.
    observed_at: int


class NoticeCoverN2Adapter:
    """One window's bounded revision mapper for source-owned Notice C covers.

    N2's integer revisions are local CAS generations. The authoritative
    documentary revision remains NoticeSourceObservation.source_revision;
    N3's source images/previews remain installation-wide.
    """

    def __init__(self, *, max_documents: int = 4096):
        if type(max_documents) is not int or not 1 <= max_documents <= 20000:
            raise ValueError("invalid notice cover adapter bound")
        self._lock = RLock()
        self._max_documents = max_documents
        self._issued = 0
        self._published: OrderedDict[CoverDocumentScope, _Published] = OrderedDict()
        self._closed = False

    def register(
        self,
        service: DocumentCoverService,
        observation: NoticeSourceObservation, *,
        field_path: tuple[str, ...],
        cover_inventory_complete: bool,
    ) -> CoverDocumentScope:
        """Derive N2 from the *replayed* exact Notice C source snapshot.

        A changed source/parser/edition changes the attested input or
        DocumentRef; a previously published N2 decision cannot survive a
        different cover field contract under the same local generation.
        """
        if type(service) is not DocumentCoverService:
            raise TypeError("expected common DocumentCoverService")
        attribute = project_notice_cover_attribute(
            observation, field_path=field_path,
            cover_inventory_complete=cover_inventory_complete,
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("notice cover adapter closed")
            previous = self._published.get(attribute.document)
            if previous is not None:
                if observation.observed_at < previous.observed_at:
                    # Out-of-order producer deliveries cannot roll back C's
                    # last accepted evidence or resurrect a stale N2 token.
                    self._published.move_to_end(attribute.document)
                    return attribute.document
                if (previous.attribute.snapshot_complete
                        and not attribute.snapshot_complete):
                    # Incomplete evidence cannot revoke an attested cover
                    # even when the parser moves the cover field. A new
                    # mapping becomes authoritative only after its inventory
                    # is complete. Keep N2's token; advance only freshness.
                    self._published[attribute.document] = _Published(
                        previous.attribute, previous.revision,
                        observation.observed_at,
                    )
                    self._published.move_to_end(attribute.document)
                    return attribute.document
            same = previous is not None and previous.attribute == attribute
            revision = previous.revision if same else self._issued + 1
            service.register(CoverSourceSnapshot(
                scope=attribute.document, revision=revision,
                candidates=attribute.candidates,
                snapshot_complete=attribute.snapshot_complete,
            ))
            if not same:
                self._issued = revision
            self._published[attribute.document] = _Published(
                attribute, revision, observation.observed_at,
            )
            self._published.move_to_end(attribute.document)
            while len(self._published) > self._max_documents:
                self._published.popitem(last=False)
            return attribute.document

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._published.clear()


__all__ = ["NoticeCoverN2Adapter"]
