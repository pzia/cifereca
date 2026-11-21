"""#4530: typed age observations of one exact public catalogue edition.

The source parser must explicitly identify the native field and bounds.
Never parse a loose age label, page facet, category, title, or another
edition's age. No provider-specific grammar, network, or rights.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from ..models import SearchResult

FIELD = "age_observation"
_WITNESS = object()
_KINDS = frozenset(("interest", "reading", "publisher", "recommended"))
_COORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")


@dataclass(frozen=True, slots=True)
class PublicAgeRange:
    age_min: int | None
    age_max: int | None
    age_kind: str
    source_label: str


@dataclass(frozen=True, slots=True)
class PublicAgeObservation:
    provider_id: str
    record_id: str
    docbase: str
    medium: str
    source_revision: str | None
    source_field: str
    ranges: tuple[PublicAgeRange, ...]

    def wire(self):
        return {"schema": 1, "provider_id": self.provider_id, "record_id": self.record_id,
                "docbase": self.docbase, "medium": self.medium,
                "source_revision": self.source_revision, "source_field": self.source_field,
                "ranges": [asdict(r) for r in self.ranges]}

    def filter_ranges(self):
        """Preserve contradictory alternatives but abstain from filtering them.

        Multiple differing native bounds of one kind are not a new inferred
        range, intersection or union. Other independent kinds remain usable.
        """
        return [asdict(r) for r in self.ranges
                if len({(x.age_min, x.age_max) for x in self.ranges
                        if x.age_kind == r.age_kind}) == 1]


def decode_public_age_observation(raw, *, provider_id, record_id, docbase, medium, source_revision):
    from .public_search_projection import _public_bibliographic_text
    if (type(raw) is not dict or set(raw) != {
            "schema", "provider_id", "record_id", "docbase", "medium", "source_revision",
            "source_field", "ranges"}
            or type(raw["schema"]) is not int or raw["schema"] != 1
            or any(type(value) is not str or _COORD.fullmatch(value) is None
                   or "://" in value or ".." in value
                   for value in (provider_id, record_id, docbase))
            or type(medium) is not str or medium not in ("digital", "physical")
            or (source_revision is not None and (
                type(source_revision) is not str or _COORD.fullmatch(source_revision) is None
                or "://" in source_revision or ".." in source_revision))
            or any(type(raw[name]) is not type(expected) or raw[name] != expected
                   for name, expected in (("provider_id", provider_id), ("record_id", record_id),
                                         ("docbase", docbase), ("medium", medium),
                                         ("source_revision", source_revision)))
            or type(raw["source_field"]) is not str
            or _COORD.fullmatch(raw["source_field"]) is None
            or not raw["source_field"].startswith(provider_id + ".")
            or "://" in raw["source_field"] or ".." in raw["source_field"]
            or type(raw["ranges"]) is not list or not 1 <= len(raw["ranges"]) <= 16):
        return None
    ranges = []
    for value in raw["ranges"]:
        if (type(value) is not dict or set(value) !=
                {"age_min", "age_max", "age_kind", "source_label"}):
            return None
        low, high, kind, label = (value["age_min"], value["age_max"],
                                  value["age_kind"], value["source_label"])
        if (type(kind) is not str or kind not in _KINDS
                or (low is None and high is None)
                or any(v is not None and (type(v) is not int or not 0 <= v <= 120)
                       for v in (low, high))
                or (low is not None and high is not None and low > high)
                or not _public_bibliographic_text(label, required=True, limit=120)):
            return None
        item = PublicAgeRange(low, high, kind, label)
        if item not in ranges:
            ranges.append(item)
    return PublicAgeObservation(provider_id, record_id, docbase, medium, source_revision,
                                raw["source_field"], tuple(ranges))


def _coords(row):
    return dict(provider_id=row.source_provider, record_id=row.record_id,
                docbase=row.docbase, medium=row.medium, source_revision=row.revision_id)


def stamp_same_call_public_age(row, original):
    """Called only by Core's verified anonymous same-call projector.

    The raw structured proof is never copied wholesale into the public row
    or generic account mirror. Bind to the final object and exact coordinates.
    """
    if type(row) is not SearchResult:
        return
    # A new same-call attempt replaces, never accumulates, the prior proof.
    # A reused/cached DTO must not keep a previously qualified age when the
    # current original has no age, malformed evidence, or different coordinates.
    row.__dict__.pop("_core_same_call_public_age", None)
    if type(original) is not SearchResult:
        return
    proof = original.metadata.get("bibliography_evidence") if type(original.metadata) is dict else None
    if type(proof) is not dict or len(proof) > 32 or _coords(row) != _coords(original):
        return
    age = decode_public_age_observation(proof.get("youth_age"), **_coords(row))
    if age is not None:
        row._core_same_call_public_age = (_WITNESS, id(row), age)


def verified_same_call_public_age(row):
    if type(row) is not SearchResult:
        return None
    receipt = getattr(row, "_core_same_call_public_age", None)
    if (type(receipt) is not tuple or len(receipt) != 3
            or receipt[0] is not _WITNESS or receipt[1] != id(row)
            or type(receipt[2]) is not PublicAgeObservation):
        return None
    return decode_public_age_observation(receipt[2].wire(), **_coords(row))


__all__ = ["PublicAgeObservation", "PublicAgeRange", "decode_public_age_observation"]
