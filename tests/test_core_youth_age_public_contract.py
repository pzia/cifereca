from __future__ import annotations

from dataclasses import FrozenInstanceError
import math

import pytest

from cifereca.plugin_api import (
    Provenance, YouthAgeRange, youth_age_from_label, youth_age_matches,
)


@pytest.mark.parametrize(("label", "age_min", "age_max"), [
    ("à partir de 8 ans", 8, None),
    ("À partir de 8 ans", 8, None),
    ("Dès 8 ans", 8, None),
    ("9+", 9, None),
    ("12 ans et plus", 12, None),
    ("10–12 ans", 10, 12),
    ("10-12 ans", 10, 12),
    ("de 10 à 12 ans", 10, 12),
    ("entre 9 et 11 ans", 9, 11),
    ("jusqu’à 6 ans", None, 6),
    ("jusqu'a 6 ans", None, 6),
    ("6 ans et moins", None, 6),
])
def test_explicit_source_age_ranges_are_normalized_without_losing_label(label, age_min, age_max):
    source = Provenance("booktrust", source_id="record-12", observed_at="2026-10-08")
    result = youth_age_from_label(label, age_kind="interest", provenance=source, confidence=0.95)
    assert result == YouthAgeRange(age_min, age_max, "interest", label, source, 0.95)


@pytest.mark.parametrize("label", [
    "", "8 ans", "à 8 ans", "8–12 ans environ", "à partir de 8 ans (niveau CM2)",
    "enfants", "dès huit ans", "7ème", "CM2", "de 12 à 8 ans", "999+",
    "12 ans, mais pas avant", "10-12 ans\n9+", "9+ et adulte", "moins de 6 ans",
    "de 8 à 12 ans pour les grands lecteurs",
])
def test_prose_and_unattested_age_inference_are_never_accepted(label):
    assert youth_age_from_label(label) is None


def test_distinguish_age_interest_and_reading_age_without_collapsing_ranges():
    source = Provenance("booktrust", source_id="title-123")
    interest = youth_age_from_label("8–12 ans", age_kind="interest", provenance=source)
    reading = youth_age_from_label("10–12 ans", age_kind="reading", provenance=source)
    assert youth_age_matches([interest, reading], 8, age_kind="interest") is True
    assert youth_age_matches([interest, reading], 8, age_kind="reading") is False
    assert youth_age_matches([interest, reading], 11, age_kind="reading") is True
    assert youth_age_matches([interest, reading], 13) is False


def test_inclusive_boundaries_and_open_ends():
    lower = youth_age_from_label("à partir de 8 ans")
    upper = youth_age_from_label("jusqu'à 6 ans")
    bounded = youth_age_from_label("10–12 ans")
    assert all(lower.contains(age) for age in [8, 9, 17])
    assert not lower.contains(7)
    assert all(upper.contains(age) for age in [0, 3, 6])
    assert not upper.contains(7)
    assert all(bounded.contains(age) for age in [10, 11, 12])
    assert not bounded.contains(9)
    assert not bounded.contains(13)
    assert not lower.contains(18)  # youth filters do not select adults


def test_inferred_and_unknown_ages_are_not_automatically_filtered_in():
    inferred = YouthAgeRange(8, 12, age_kind="inferred", source_label="estimation")
    assert not youth_age_matches([inferred], 10)
    assert youth_age_matches([inferred], 10, include_inferred=True)
    unknown = YouthAgeRange(source_label="aucun âge qualifié")
    assert not youth_age_matches([unknown], 0)
    assert not youth_age_matches([unknown], 10)
    assert not youth_age_matches([unknown], 17)
    assert not youth_age_matches([None, {}, "8+", unknown], 8)


def test_min_confidence_requires_an_attested_value():
    high = YouthAgeRange(8, 12, confidence=0.9)
    low = YouthAgeRange(8, 12, confidence=0.3)
    not_graded = YouthAgeRange(8, 12)
    assert youth_age_matches([low, not_graded], 9)
    assert not youth_age_matches([low, not_graded], 9, min_confidence=0.8)
    assert youth_age_matches([high], 9, min_confidence=0.8)
    assert youth_age_matches([high, low], 9, min_confidence=0.8)


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"age_min": -1}, "age_min"),
    ({"age_max": 121}, "age_max"),
    ({"age_min": True}, "age_min"),
    ({"age_min": "8"}, "age_min"),
    ({"age_min": 12, "age_max": 10}, "age_min exceeds"),
    ({"age_kind": "unspecified"}, "kind"),
    ({"confidence": math.inf}, "confidence"),
    ({"confidence": math.nan}, "confidence"),
    ({"confidence": 1.1}, "confidence"),
    ({"confidence": -0.1}, "confidence"),
    ({"confidence": True}, "confidence"),
])
def test_model_rejects_invalid_age_metadata(kwargs, message):
    with pytest.raises(ValueError, match=message):
        YouthAgeRange(**kwargs)


def test_public_age_model_is_immutable_and_does_not_invent_provenance():
    result = youth_age_from_label("10-12 ans")
    assert result.provenance is None
    assert result.confidence is None
    with pytest.raises(FrozenInstanceError):
        result.age_min = 0
    assert result.contains(True) is False
    assert youth_age_matches([result], True) is False
