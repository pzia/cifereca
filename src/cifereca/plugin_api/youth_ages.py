"""Strict, source-preserving youth age normalization for public plugins.

Only standalone, explicit age recommendations are parsed. No title, grade,
subject or author is used to infer a target age.
"""
from __future__ import annotations

from collections.abc import Iterable
import re

from .models import Provenance, YouthAgeKind, YouthAgeRange


def youth_age_from_label(
    label: str,
    *,
    age_kind: YouthAgeKind = "recommended",
    provenance: Provenance | None = None,
    confidence: float | None = None,
) -> YouthAgeRange | None:
    """Parse an explicit French range; ambiguous descriptions stay unknown.

    Examples: « à partir de 8 ans », « 10–12 ans », « jusqu'à 6 ans », « 9+ ».
    Returns None for « 8 ans » (exact vs minimum is not attested), book titles,
    school grades and free prose. The original label is retained verbatim.
    """
    if not isinstance(label, str) or not 0 < len(label) <= 120:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in label):
        return None
    text = " ".join(label.casefold().strip().replace("’", "'").split())
    text = text.translate(str.maketrans("–—−", "---"))
    years = r"(\d{1,3})"

    lower = (
        rf"(?:à partir de|a partir de|dès|des)\s+{years}\s*ans?",
        rf"{years}\s*\+",
        rf"{years}\s*ans?\s*(?:et plus|ou plus)",
    )
    upper = (
        rf"jusqu[' ]?à\s+{years}\s*ans?",
        rf"jusqu[' ]?a\s+{years}\s*ans?",
        rf"{years}\s*ans?\s*(?:et moins|ou moins)",
    )
    bounded = (
        rf"{years}\s*-\s*{years}\s*ans?",
        rf"(?:de|entre)\s+{years}\s*(?:à|a|et)\s*{years}\s*ans?",
    )

    try:
        for pattern in bounded:
            match = re.fullmatch(pattern, text)
            if match:
                return YouthAgeRange(int(match[1]), int(match[2]), age_kind, label, provenance, confidence)
        for pattern in lower:
            match = re.fullmatch(pattern, text)
            if match:
                return YouthAgeRange(age_min=int(match[1]), age_kind=age_kind,
                                     source_label=label, provenance=provenance, confidence=confidence)
        for pattern in upper:
            match = re.fullmatch(pattern, text)
            if match:
                return YouthAgeRange(age_max=int(match[1]), age_kind=age_kind,
                                     source_label=label, provenance=provenance, confidence=confidence)
    except ValueError:
        # A range such as 12–8 or an out-of-domain age is not evidence.
        return None
    return None


def youth_age_matches(
    ranges: Iterable[YouthAgeRange],
    age: int,
    *,
    age_kind: YouthAgeKind | None = None,
    include_inferred: bool = False,
    min_confidence: float | None = None,
) -> bool:
    """True only when an explicit normalized range covers this child's age.

    By default inferred ages and unknown ranges never satisfy the filter.
    Unknown confidence passes only when no minimum confidence was requested.
    """
    for value in ranges:
        if not isinstance(value, YouthAgeRange):
            continue
        if value.age_kind == "inferred" and not include_inferred:
            continue
        if age_kind is not None and value.age_kind != age_kind:
            continue
        if min_confidence is not None and (
            value.confidence is None or value.confidence < min_confidence
        ):
            continue
        if value.contains(age):
            return True
    return False


__all__ = ["youth_age_from_label", "youth_age_matches"]
