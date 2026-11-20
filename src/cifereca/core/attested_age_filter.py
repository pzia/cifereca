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
    # Both legacy and current names can coexist in a projected row.
    # A contradiction or malformed nonempty alias is not exact evidence.
    selected = ""
    for name in names:
        value = row.get(name)
        if value is None or (type(value) is str and not value):
            continue
        if (type(value) is not str or len(value) > 256
                or not value.strip() or not value.isprintable()
                or (selected and value != selected)):
            return ""
        selected = value
    return selected


def _age_provenance_matches(proof: object, provider: str, edition: str) -> bool:
    if proof is None:
        return True
    if type(proof) is Provenance:
        source, provider_id, source_id = proof.source, proof.provider_id, proof.source_id
    elif type(proof) is dict:
        source = proof.get("source")
        provider_id = proof.get("provider_id", "")
        source_id = proof.get("source_id", "")
    else:
        return False
    if (type(source) is not str or not source
            or type(provider_id) is not str or type(source_id) is not str):
        return False
    if source == provider and provider_id and provider_id != provider:
        return False
    # An independent authority may have its own record ID. When either
    # coordinate identifies this provider, its record must be this edition.
    if source == provider or provider_id == provider:
        return source_id in ("", edition)
    return True


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
            if (
                (provenance is not None and type(provenance) is not Provenance)
                or not _age_provenance_matches(provenance, provider, edition)
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
        if not _age_provenance_matches(record.get("provenance"), provider, edition) or any(
            record.get(name) is not None
            and (type(record[name]) is not str or record[name] != expected)
            for names, expected in (
                (("provider_id", "source_provider"), provider),
                (("item_id", "record_id"), edition),
            )
            for name in names
        ):
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
