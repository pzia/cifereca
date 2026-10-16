"""#1599: malformed cached age fields must not look like attestations.

Headless list/detail/BookRef projection, no GTK/network/acquisition or files.
"""
from __future__ import annotations

from youth_catalog import YouthAgeRange
from youth_page import book_ref, detail_presentation, list_entry
from tests.test_youth_page import youth_book


class Poison:
    def __str__(self):
        raise AssertionError("Bearer PRIVATE_CREDENTIAL must not be formatted")


class SecretString(str):
    pass


def ages(detail):
    section = next(s for s in detail.sections if s.id == "ages")
    return [f.value for f in section.fields]


def test_invalid_age_container_is_not_rendered_as_attested_values():
    good = YouthAgeRange(8, 10, "interest", "Public", "milan")
    for raw in (
        {"PRIVATE_TOKEN": good},
        "PRIVATE_TOKEN",
        SecretString("PRIVATE_TOKEN"),
        Poison(),
        (),
    ):
        book = youth_book(age_ranges=raw)
        entry = list_entry(book)
        detail = detail_presentation(book)
        origin = book_ref(book)
        assert entry.badge == "Âge inconnu"
        assert "PRIVATE" not in repr((entry, detail, origin))
        assert ages(detail) == ["Aucune tranche d’âge attestée en cache."]
        assert origin.metadata["youth_age_ranges"] == ()


def test_mixed_cache_ages_keep_real_ranges_without_coercing_provider_objects():
    attested = YouthAgeRange(8, 10, "reading", Poison(), "PRIVATE\nTOKEN", 0.75)
    more = YouthAgeRange(12, None, "publisher", "À partir de 12 ans", "milan", 1.0)
    book = youth_book(age_ranges=(
        {"PRIVATE_FIELD": "age"},
        Poison(),
        attested, more,
        SecretString("PRIVATE_TOKEN"),
    ))
    entry, detail, origin = list_entry(book), detail_presentation(book), book_ref(book)
    assert entry.badge == "Lecture 8–10 ans"
    assert len(ages(detail)) == 2
    assert "Âge de lecture · 8–10 ans" in ages(detail)[0]
    assert "confiance 75 %" in ages(detail)[0]
    assert "source milan" in ages(detail)[1]
    assert origin.metadata["youth_age_ranges"] == (
        {
            "age_min": 8, "age_max": 10, "age_kind": "reading",
            "source_label": "", "source_id": "", "confidence": 0.75,
        },
        {
            "age_min": 12, "age_max": None, "age_kind": "publisher",
            "source_label": "À partir de 12 ans", "source_id": "milan",
            "confidence": 1.0,
        },
    )
    assert "PRIVATE" not in repr((entry, detail, origin))


def test_forged_age_range_and_nonfinite_confidence_never_leak():
    forged = YouthAgeRange(7, 9)
    object.__setattr__(forged, "age_min", -1)
    object.__setattr__(forged, "age_kind", "PRIVATE_UNKNOWN_KIND")
    other = YouthAgeRange(10, 12, "recommended", "PRIVATE\u202eCONTROL", "milan")
    object.__setattr__(other, "confidence", float("inf"))
    book = youth_book(age_ranges=(forged, other))
    detail = detail_presentation(book)
    origin = book_ref(book)
    assert len(ages(detail)) == 1
    assert "10–12 ans" in ages(detail)[0]
    assert "PRIVATE" not in repr((detail, origin))
    assert origin.metadata["youth_age_ranges"][0]["confidence"] is None


def test_age_ranges_are_bounded_but_preserve_independently_attested_sources():
    ranges = tuple(
        YouthAgeRange(8, 10, "interest", f"Source {i}", "milan")
        for i in range(90)
    )
    book = youth_book(age_ranges=ranges)
    entry, detail, origin = list_entry(book), detail_presentation(book), book_ref(book)
    assert len(ages(detail)) == 32
    assert len(origin.metadata["youth_age_ranges"]) == 32
    assert "Source 31" in ages(detail)[31]
    assert "Source 32" not in str(ages(detail))
    assert entry.badge == "Intérêt 8–10 ans"
