"""#2311: general Search may filter only proven ages of the exact edition."""
from __future__ import annotations

import pytest

from cifereca.core.attested_age_filter import (
    attested_age_ranges, attested_age_status, filter_attested_age,
)
from cifereca.plugin_api import Provenance, YouthAgeRange, youth_age_from_label


def book(item_id, *, title="Le même titre", ages=None, provider="bnf"):
    return {
        "provider_id": provider, "item_id": item_id, "title": title,
        "metadata": {"youth_age_ranges": ages} if ages is not None else {},
    }


def statement(low, high, kind="publisher", label="Dès 8 ans"):
    return {"age_min": low, "age_max": high,
            "age_kind": kind, "source_label": label}


def test_open_intervals_and_unknown_vs_incompatible():
    above = book("above", ages=[statement(8, None)])
    below = book("below", ages=[statement(None, 6, "interest")])
    unknown = book("unknown", ages=None)
    invalid = book("invalid", ages=[statement(13, 8)])
    assert attested_age_status(above, 8) == "match"
    assert attested_age_status(above, 7) == "incompatible"
    assert attested_age_status(below, 6) == "match"
    assert attested_age_status(below, 7) == "incompatible"
    assert attested_age_status(unknown, 8) == "unknown"
    assert attested_age_status(invalid, 8) == "unknown"
    assert filter_attested_age([above, below, unknown, invalid], 8) == [above]
    assert filter_attested_age([above, below, unknown, invalid], None) == [
        above, below, unknown, invalid,
    ]


def test_two_homonymous_editions_do_not_share_the_proof():
    edition_a = book("9780306406157", ages=[statement(8, 10)])
    edition_b = book("9780140328721")
    same_other_provider = book("9780306406157", ages=[statement(13, 16)], provider="kobo")
    assert filter_attested_age([edition_a, edition_b, same_other_provider], 9) == [
        edition_a,
    ]
    assert filter_attested_age([edition_a, edition_b, same_other_provider], 14) == [
        same_other_provider,
    ]


def test_age_kind_is_not_conflated_and_inference_is_never_attested():
    record = book("item", ages=[
        statement(8, 10, "interest"),
        statement(11, 14, "reading"),
        statement(7, 15, "inferred"),
    ])
    assert attested_age_status(record, 9, age_kind="interest") == "match"
    assert attested_age_status(record, 9, age_kind="reading") == "incompatible"
    assert attested_age_status(record, 13, age_kind="reading") == "match"
    assert attested_age_status(record, 7) == "incompatible"
    assert attested_age_status(record, 7, age_kind="publisher") == "unknown"
    assert attested_age_status(record, 9, age_kind="unknown") == "unknown"


@pytest.mark.parametrize("bad", [
    statement(8, 10, "inferred"),
    statement(True, 10),
    statement(-1, 10),
    statement("8", 10),
    statement(14, 10),
    statement(None, None),
    {"age_min": 8, "age_max": 10, "age_kind": "PRIVATE"},
    {"age_min": 8, "age_max": 10, "age_kind": "publisher",
     "source_label": "PRIVATE" * 100},
    {"age_min": 8, "age_max": 10, "age_kind": True},
    "8+",
    8,
])
def test_malformed_and_inferred_values_never_match(bad):
    assert attested_age_status(book("item", ages=[bad]), 9) == "unknown"


def test_source_and_edition_provenance_is_rebuilt_from_exact_row():
    age = book("edition-123", ages=[statement(8, 12)])
    attested = attested_age_ranges(age)
    assert len(attested) == 1
    assert attested[0].provenance == Provenance("bnf", source_id="edition-123")
    assert attested[0].source_label == "Dès 8 ans"
    assert attested[0].age_kind == "publisher"
    for unusable in [
        {"provider_id": "bnf", "title": "An edition", "metadata": age["metadata"]},
        {"item_id": "edition-123", "title": "An edition", "metadata": age["metadata"]},
        {"provider_id": "bnf", "item_id": True, "metadata": age["metadata"]},
        {"provider_id": "bnf", "item_id": "edition-123", "metadata": {
            "genre": "jeunesse", "target_age": "8+",
        }},
    ]:
        assert attested_age_status(unusable, 9) == "unknown"


def test_existing_public_age_model_is_reused_without_parsing_titles_or_prose():
    attest = youth_age_from_label(
        "10-12 ans", age_kind="publisher",
        provenance=Provenance("bnf", source_id="edition"),
    )
    assert attest is not None
    structured = book("edition", ages=[attest])
    assert attested_age_status(structured, 11) == "match"
    assert attested_age_ranges(structured)[0] is attest
    assert attested_age_status(book("another", title="10-12 ans et jeunes lecteurs"), 11) == "unknown"
    # Even an exact title match may not transfer an age from the other edition.
    foreign = youth_age_from_label(
        "10-12 ans", age_kind="publisher",
        provenance=Provenance("bnf", source_id="another-edition"),
    )
    assert attested_age_status(book("edition", ages=[foreign]), 11) == "unknown"
    assert attested_age_status(book("edition", ages=[
        {**statement(10, 12), "item_id": "another-edition"},
    ]), 11) == "unknown"
    assert attested_age_status(book("edition", ages=[
        {**statement(10, 12), "provider_id": "other"},
    ]), 11) == "unknown"


def test_no_scan_or_network_and_bounded_input_inventory():
    calls = []
    rows = [book("a", ages=[statement(8, 12)]), book("b")]
    def iterate():
        for row in rows:
            calls.append(row["item_id"])
            yield row
    assert [r["item_id"] for r in filter_attested_age(iterate(), 9)] == ["a"]
    assert calls == ["a", "b"]
    assert attested_age_status(book("large", ages=[statement(8, 12)] * 33), 9) == "unknown"
    assert attested_age_status(book("item", ages=[statement(8, 12)]), True) == "unknown"
    assert attested_age_status(book("item", ages=[statement(8, 12)]), 18) == "unknown"
