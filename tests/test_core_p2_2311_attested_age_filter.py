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
    publisher = youth_age_from_label(
        "10-12 ans", age_kind="publisher",
        provenance=Provenance("éditeur-cité", source_id="éditeur-record"),
    )
    # A publisher may have an independent source-record identifier; its
    # provenance is preserved rather than silently remapped to the provider.
    allowed = attested_age_ranges(book("edition", ages=[publisher]))
    assert allowed == (publisher,)
    assert allowed[0].provenance.source == "éditeur-cité"


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


@pytest.mark.parametrize("aliases", [
    {"source_provider": "other"},
    {"record_id": "foreign-edition"},
    {"source_provider": True},
    {"record_id": ["edition"]},
    {"provider_id": "bnf\n", "source_provider": "bnf\n"},
])
def test_conflicting_or_malformed_row_coordinates_are_unknown(aliases):
    row = book("edition", ages=[statement(8, 12)])
    row.update(aliases)
    assert attested_age_status(row, 9) == "unknown"


@pytest.mark.parametrize("aliases", [
    {"source_provider": "other"},
    {"record_id": "foreign-edition"},
    {"source_provider": "bnf", "record_id": False},
])
def test_conflicting_statement_aliases_are_not_attested(aliases):
    row = book("edition", ages=[{**statement(8, 12), **aliases}])
    assert attested_age_status(row, 9) == "unknown"


def test_exact_aliases_and_legacy_absent_aliases_remain_compatible():
    row = book("edition", ages=[{
        **statement(8, 12), "source_provider": "bnf", "record_id": "edition",
    }])
    row.update(source_provider="bnf", record_id="edition")
    assert attested_age_status(row, 9) == "match"
    row.update(provider_id=None, item_id="")
    assert attested_age_status(row, 9) == "match"


@pytest.mark.parametrize("proof", [
    {"source": "bnf", "source_id": "paper-edition"},
    {"source": "cnlj", "provider_id": "bnf", "source_id": "paper-edition"},
    Provenance("bnf", source_id="paper-edition"),
    Provenance("cnlj", provider_id="bnf", source_id="paper-edition"),
    {"source": "bnf", "source_id": []},
])
@pytest.mark.parametrize("typed", [False, True])
def test_nested_provenance_cannot_move_proof_to_another_edition(proof, typed):
    age = (YouthAgeRange(8, 12, "publisher", provenance=proof) if typed
           else {**statement(8, 12), "provenance": proof})
    assert attested_age_status(book("ebook-edition", ages=[age]), 9) == "unknown"


@pytest.mark.parametrize("proof", [
    {"source": "bnf", "source_id": "edition"},
    {"source": "cnlj", "provider_id": "bnf", "source_id": "edition"},
    Provenance("éditeur", source_id="publisher-record"),
    {"source": "éditeur", "source_id": "publisher-record"},
])
def test_nested_provenance_accepts_exact_edition_or_independent_authority(proof):
    row = book("edition", ages=[{**statement(8, 12), "provenance": proof}])
    assert attested_age_status(row, 9) == "match"

@pytest.mark.parametrize("label", [
    "x" * 121,
    ["à partir de 8 ans"],
    {"raw": "PRIVATE"},
    None,
])
def test_typed_age_label_obeys_same_bounded_native_wire_contract(label):
    # YouthAgeRange validates ages and confidence at construction, but does
    # not enforce the declared type/length of its source_label field.
    # A plugin-supplied typed object must not bypass the dict-wire fence.
    typed = YouthAgeRange(8, 12, "publisher", source_label=label,
                          provenance=Provenance("bnf", source_id="edition"))
    assert attested_age_ranges(book("edition", ages=[typed])) == ()
    assert attested_age_status(book("edition", ages=[typed]), 9) == "unknown"


def test_typed_age_label_with_valid_exact_provenance_remains_attested():
    valid = YouthAgeRange(8, 12, "publisher", source_label="x" * 120,
                          provenance=Provenance("bnf", source_id="edition"))
    assert attested_age_ranges(book("edition", ages=[valid])) == (valid,)
    assert attested_age_status(book("edition", ages=[valid]), 9) == "match"
    # An invalid typed sibling cannot replace independently valid evidence.
    bad = YouthAgeRange(8, 12, "publisher", source_label="x" * 121)
    assert attested_age_ranges(book("edition", ages=[bad, valid])) == (valid,)



@pytest.mark.parametrize("mutation", [
    ("age_min", True), ("age_min", "8"), ("age_min", -1),
    ("age_min", 121), ("age_max", 7), ("age_max", 121),
    ("age_kind", None), ("confidence", float("nan")),
    ("confidence", float("inf")), ("confidence", True),
])
def test_post_construction_mutated_typed_age_cannot_pass_filter(mutation):
    # Frozen dataclasses are not a trust boundary for a plugin DTO: callers
    # may use object.__setattr__, including after an earlier positive match.
    typed = YouthAgeRange(
        8, 12, "publisher", source_label="8 à 12 ans",
        provenance=Provenance("bnf", source_id="edition"),
    )
    row = book("edition", ages=[typed])
    assert attested_age_status(row, 9) == "match"
    name, value = mutation
    object.__setattr__(typed, name, value)
    assert attested_age_ranges(row) == ()
    assert attested_age_status(row, 9) == "unknown"
    assert filter_attested_age([row], 9) == []


def test_mutated_typed_age_does_not_invoke_hostile_kind_or_bounds():
    class Hostile:
        def __eq__(self, other):
            raise AssertionError("supplier-defined equality invoked")

        def __hash__(self):
            raise AssertionError("supplier-defined hash invoked")

        def __le__(self, other):
            raise AssertionError("supplier-defined ordering invoked")

    for attr in ("age_kind", "age_min", "age_max"):
        typed = YouthAgeRange(8, 12, "publisher", source_label="8 à 12 ans")
        object.__setattr__(typed, attr, Hostile())
        row = book("edition", ages=[typed])
        assert attested_age_ranges(row) == ()
        assert attested_age_status(row, 9) == "unknown"
