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
    return type(actual) is type(expected) and actual == expected


def primary_known_public_c_ages(item):
    """Retain only still-identical, unambiguous source-specific age ranges."""
    hit = known_public_primary_facts(item)
    if hit is None or hit.age_observation is None:
        return ()
    age = hit.age_observation
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
