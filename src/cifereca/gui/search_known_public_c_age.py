"""Source-bound public C age in Search detail; no inference or provider I/O.

The existing native book detail renderer understands YouthAgeRange.
Only the exact Core primary C hit can provide typed provenance here.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..core.notice_c_known_projection import known_public_primary_facts
from ..plugin_api import Provenance, YouthAgeRange


def _same_native(actual, expected, depth=0):
    """Bounded structural comparison; never invoke supplier-defined equality."""
    if depth > 5:
        return False
    if type(expected) is dict:
        return (type(actual) is dict and len(actual) == len(expected)
                and all(type(key) is str and key in expected for key in actual)
                and all(_same_native(actual[key], value, depth + 1)
                        for key, value in expected.items()))
    if type(expected) is list:
        return (type(actual) is list and len(actual) == len(expected)
                and len(actual) <= 16
                and all(_same_native(left, right, depth + 1)
                        for left, right in zip(actual, expected)))
    # Exact matching object types are NOT enough: two identical hostile
    # instances still execute plugin-defined __eq__. The proof wire format
    # contains only these native scalar types, never arbitrary DTO objects.
    if type(expected) not in (str, int, bool, float, type(None)):
        return False
    return type(actual) is type(expected) and actual == expected


def _qualified_primary_age(age, hit):
    # Frozen public C facts can still be replaced inside a copied Core
    # packet. Validate their entire native payload before calling .wire()
    # or filter_ranges(), whose set membership would hash foreign bounds.
    from ..core.notice_c_public_age import (
        PublicAgeObservation, PublicAgeRange, decode_public_age_observation,
    )
    if (type(age) is not PublicAgeObservation
            or any(type(getattr(age, name)) is not str for name in (
                "provider_id", "record_id", "docbase", "medium", "source_field",
            ))
            or (age.source_revision is not None
                and type(age.source_revision) is not str)
            or type(age.ranges) is not tuple or not 1 <= len(age.ranges) <= 16):
        return None
    for part in age.ranges:
        if (type(part) is not PublicAgeRange
                or any(value is not None and type(value) is not int for value
                       in (part.age_min, part.age_max))
                or type(part.age_kind) is not str
                or type(part.source_label) is not str
                or len(part.source_label) > 120):
            return None
    return decode_public_age_observation(
        age.wire(), provider_id=hit.provider_id, record_id=hit.record_id,
        docbase=hit.docbase, medium=hit.medium,
        source_revision=hit.source_revision,
    )


def primary_known_public_c_ages(item):
    """Retain only still-identical, unambiguous source-specific age ranges."""
    hit = known_public_primary_facts(item)
    if hit is None or hit.age_observation is None:
        return ()
    age = _qualified_primary_age(hit.age_observation, hit)
    if age is None:
        return ()
    meta = item.get("metadata")
    if type(meta) is not dict:
        return ()
    evidence = meta.get("bibliography_evidence")
    ranges = age.filter_ranges()
    if (not ranges or type(evidence) is not dict
            or not _same_native(evidence.get("youth_age"), age.wire())
            or not _same_native(meta.get("youth_age_ranges"), ranges)):
        return ()
    try:
        observed = datetime.fromtimestamp(hit.observed_at, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError, TypeError):
        return ()
    provenance = Provenance(
        source=age.source_field,
        provider_id=hit.provider_id,
        source_id=hit.record_id,
        observed_at=observed,
    )
    return tuple(YouthAgeRange(
        age_min=value["age_min"], age_max=value["age_max"],
        age_kind=value["age_kind"], source_label=value["source_label"],
        provenance=provenance,
    ) for value in ranges)


__all__ = ["primary_known_public_c_ages"]
