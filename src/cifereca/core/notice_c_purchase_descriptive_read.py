"""Read-only private purchase descriptive contributions for a Core view.

A commercial product is NOT the acquired manifestation. Keep its descriptive
facts separate, carry their original source provenance (not receipt refresh
time), and never overwrite local, acquired or other-provider metadata.
No provider discovery, search, GET, rights, or UI lifecycle in this reader.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..plugin_api.downloadable_resources import SourceReference
from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import _db_key, _decode
from .notice_c_purchase_association import _read_txn


@dataclass(frozen=True, slots=True, repr=False)
class PurchaseDescriptiveFact:
    field: str
    value: str
    provider_id: str
    source_ref: str
    source_revision: str
    observed_at: int


@dataclass(frozen=True, slots=True, repr=False)
class PurchaseDescriptiveNotice:
    document: CoverDocumentScope
    canonical_epoch: int
    association_observed_at: int
    facts: tuple[PurchaseDescriptiveFact, ...]

    @property
    def proves_acquired_edition(self):
        return False

    @property
    def authorizes_acquisition(self):
        return False


_FIELDS = ("title", "author", "publisher", "description", "published_at")


def read_purchase_descriptive_notice(storage, source, *, identity_id):
    """Exactly one scoped A->C receipt; single read snapshot, fail closed.

    The caller supplies its captured authorized profile and must revalidate
    the view generation before presenting this result. No global lookup by
    title, ISBN or public product ID. Values belong to the public product;
    ISBN, format, DRM, rights and physical/variant claims are intentionally
    not projected. Association freshness cannot redate descriptive facts.
    """
    if (type(source) is not SourceReference or source.kind != "purchase"
            or type(identity_id) is not str or not identity_id
            or source.scope.identity_id != identity_id):
        return None
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        return _read_descriptive_txn(db, source)


def _read_descriptive_txn(db, source):
    """Same bounded decoder, also usable by a Core batch read snapshot."""
    try:
        link = _read_txn(db, source)
        if link is None:
            return None
        raw = db.execute("SELECT value FROM metadata WHERE key=?",
                         (_db_key(link.document),)).fetchone()
        if raw is None:
            return None
        notice = _decode(raw[0], link.document)
        if len(notice.sources) != 1:
            return None
        member = notice.sources[0]
        if member.visibility != "public" or member.public_bibliography is None:
            return None
        bibliographic = member.public_bibliography
        facts = []
        values = [(field, getattr(bibliographic, field)) for field in _FIELDS]
        # Retain only already decoded, source-scoped typed observations.
        # No native format/language/filter or purchased identity overwritten.
        if bibliographic.language_observation is not None:
            values.append(("languages", " ; ".join(bibliographic.language_observation.languages)))
        if bibliographic.series_relation is not None:
            values.append(("series", bibliographic.series_relation.label))
        if bibliographic.classification is not None:
            for field, raw in bibliographic.classification.entries:
                value = raw if type(raw) is str else " ; ".join(x for x in raw if x.strip())
                if value.strip():
                    values.append((field, value))
        for field, value in values:
            if value is None:
                continue
            if type(value) is not str or not value.strip():
                return None
            facts.append(PurchaseDescriptiveFact(
                field, value, member.provider_id, member.source_ref,
                member.source_revision, member.observed_at,
            ))
        if not facts:
            return None
        return PurchaseDescriptiveNotice(
            link.document, notice.epoch, link.observed_at, tuple(facts),
        )
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        return None


__all__ = ["PurchaseDescriptiveNotice", "PurchaseDescriptiveFact",
           "read_purchase_descriptive_notice"]
