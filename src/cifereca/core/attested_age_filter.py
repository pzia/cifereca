"""Edition-scoped, cache-only age filtering for global Search (#2311).

This does not fetch a youth catalogue, infer an age from a book's text, or
merge one edition's recommendation into another. UI visibility is owned by
CoreUI; this module only projects the existing public youth-age contract.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from cifereca.plugin_api import Provenance, YouthAgeRange, youth_age_matches

AgeFilterStatus = Literal["match", "incompatible", "unknown"]
_AGE_KINDS = frozenset({"interest", "reading", "publisher", "recommended"})
_MAX_SOURCE_RANGES = 32


def _native_identifier(row: Mapping[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = row.get(name)
        if type(value) is str and value and len(value) <= 256:
            return value
    return ""


def attested_age_ranges(row: Mapping[str, Any]) -> tuple[YouthAgeRange, ...]:
    """Only an edition-linked explicit structured age is usable as evidence.

    A loose age label, category, title, inferred/LLM age, or another edition's
    bibliographic metadata cannot attest the current row. The originating
    provider and exact item/record ID are required, not reconstructed by title.
    """
    if type(row) is not dict:
        return ()
    provider = _native_identifier(row, ("provider_id", "source_provider"))
    edition = _native_identifier(row, ("item_id", "record_id"))
    if not provider or not edition:
        return ()
    metadata = row.get("metadata")
    metadata = metadata if type(metadata) is dict else {}
    supplied = row.get("youth_age_ranges")
    if supplied is None:
        supplied = metadata.get("youth_age_ranges")
    if type(supplied) not in (tuple, list) or len(supplied) > _MAX_SOURCE_RANGES:
        # A truncated inventory is not reliable evidence of complete coverage.
        return ()
    out: list[YouthAgeRange] = []
    source = Provenance(source=provider, source_id=edition)
    for record in supplied:
        if type(record) is YouthAgeRange:
            provenance = record.provenance
            if provenance is not None and (
                type(provenance) is not Provenance
                or type(provenance.source) is not str
                or not provenance.source
                # A provenance keyed to the same provider may not point at
                # another edition. A distinct publisher/authority may carry
                # its own source record ID and must retain that provenance.
                or (provenance.source == provider and provenance.source_id
                    and provenance.source_id != edition)
            ):
                continue
            if record.age_kind in _AGE_KINDS and (
                record.age_min is not None or record.age_max is not None
            ):
                out.append(record)
            continue
        if type(record) is not dict:
            continue
        # Embedded source/edition provenance must never contradict the
        # surrounding already-identified bibliographic edition.
        declared_provider = record.get("provider_id")
        declared_edition = record.get("item_id")
        if (declared_provider is not None
            and (type(declared_provider) is not str or declared_provider != provider)
        ) or (declared_edition is not None
              and (type(declared_edition) is not str or declared_edition != edition)):
            continue
        kind = record.get("age_kind")
        low, high = record.get("age_min"), record.get("age_max")
        if type(kind) is not str or kind not in _AGE_KINDS:
            continue
        if (low is None and high is None) or any(
            value is not None and (type(value) is not int or not 0 <= value <= 120)
            for value in (low, high)
        ) or (low is not None and high is not None and low > high):
            continue
        label = record.get("source_label", "")
        if type(label) is not str or len(label) > 120:
            continue
        # The row's provider/item pair attests where the structured statement
        # was seen; an arbitrary record-provided provenance may not override it.
        out.append(YouthAgeRange(low, high, kind, label, source))
    return tuple(out)


def attested_age_status(
    row: Mapping[str, Any], age: int, *, age_kind: str | None = None,
) -> AgeFilterStatus:
    """Distinguish unknown evidence from a known but incompatible age."""
    if type(age) is not int or not 0 <= age < 18:
        return "unknown"
    if age_kind is not None and (type(age_kind) is not str or age_kind not in _AGE_KINDS):
        return "unknown"
    ranges = tuple(
        item for item in attested_age_ranges(row)
        if age_kind is None or item.age_kind == age_kind
    )
    if not ranges:
        return "unknown"
    return "match" if youth_age_matches(ranges, age, age_kind=age_kind) else "incompatible"


def filter_attested_age(
    rows: Iterable[Mapping[str, Any]], age: int | None, *, age_kind: str | None = None,
) -> list[Mapping[str, Any]]:
    """Pure local filter. None means disabled, including books of unknown age."""
    if age is None:
        return list(rows)
    return [
        row for row in rows
        if attested_age_status(row, age, age_kind=age_kind) == "match"
    ]


__all__ = ["AgeFilterStatus", "attested_age_ranges", "attested_age_status",
           "filter_attested_age"]
