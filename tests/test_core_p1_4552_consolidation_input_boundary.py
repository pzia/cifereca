"""#4552: edition-exact evidence before inherited description, no Core reducer."""
from dataclasses import replace

import pytest

from cifereca.core.notice_consolidation_inputs import (
    MAX_FACTS, admit_descriptive_facts,
)

SCOPE = {
    "target_edition_id": "isbn:9780306406157",
    "canonical_type": "age",
    "consolidator_id": "org.cifereca.age.reducer",
    "consolidator_version": "1.0.0",
}


def fact(edition="isbn:9780306406157", value="13_plus", source="bnf",
         eid="age-1", type_name="age"):
    return {
        "source_id": source, "canonical_type": type_name,
        "edition_id": edition, "evidence_id": eid, "value": value,
        "source_revision": "rev-7",
    }


def resolve(facts, links=None, **changes):
    return admit_descriptive_facts(
        **(SCOPE | changes), source_facts=facts,
        attested_strong_links={} if links is None else links,
    )


def test_exact_before_inherited_never_promotes_inherited_to_exact():
    result = resolve([
        fact(edition="edition:paper", value="10_plus", eid="paper"),
        fact(value="13_plus"),
        fact(edition="edition:unrelated", eid="foreign", value="0_plus"),
    ], {"edition:paper": "ark:strong_link1"})
    assert [x.value for x in result.exact] == ["13_plus"]
    assert [x.value for x in result.inherited] == ["10_plus"]
    assert result.inherited[0].inherited is True
    assert result.inherited[0].relation == "strong_attested"
    assert result.inherited[0].relation_proof_id == "ark:strong_link1"
    assert result.exact[0].inherited is False
    assert result.exact[0].relation_proof_id is None
    assert result.rejected_count == 1


def test_same_title_or_isbn_string_without_attested_link_cannot_inherit():
    result = resolve([fact(edition="edition:paper", value="13_plus")])
    assert result.exact == () and result.inherited == ()
    assert result.rejected_count == 1


def test_different_types_are_separated_without_hardcoded_age_logic():
    result = resolve([
        fact(type_name="genre"), fact(value="12_plus"),
    ])
    assert len(result.exact) == 1
    assert result.exact[0].value == "12_plus"
    future = resolve([
        fact(type_name="canonical.custom_42")
    ], canonical_type="canonical.custom_42")
    assert len(future.exact) == 1


def test_input_order_does_not_decide_and_duplicate_is_not_independent():
    rows = [
        fact(source="beta", eid="b"), fact(source="alpha", eid="a"),
        fact(source="alpha", eid="a"),
    ]
    assert resolve(rows).exact == resolve(list(reversed(rows))).exact
    assert len(resolve(rows).exact) == 2


def test_conflicting_duplicate_proof_fails_closed():
    with pytest.raises(ValueError, match="conflicting_source_evidence"):
        resolve([fact(), fact(value="18_plus")])


@pytest.mark.parametrize("source", [
    [{"canonical_type": "age", "edition_id": "isbn:9780306406157",
      "source_id": "secret", "evidence_id": "x", "value": "a",
      "source_revision": "rev1", "account": "private"}],
    [fact(value="x" * 2049)],
    [fact(edition="private url?token=secret")],
    [None],
])
def test_unqualified_rows_do_not_enter_either_provenance_bucket(source):
    result = resolve(source)
    assert result.rejected_count == len(source) or (
        source and "account" in source[0] and len(result.exact) == 1
    )


def test_account_extras_never_emerge_in_reduced_output():
    row = fact()
    row.update(account="private", token="never-publish",
               acquisition_offer="private")
    result = resolve([row])
    assert len(result.exact) == 1
    assert "private" not in str(result)
    assert "never-publish" not in str(result)


@pytest.mark.parametrize(("rows", "links"), [
    ([fact()], {"edition:paper": "bad proof with spaces"}),
    ([fact()], {"isbn:9780306406157": "self:proof"}),
    ([fact()] * (MAX_FACTS + 1), {}),
])
def test_invalid_batches_are_rejected_without_partial_projection(rows, links):
    with pytest.raises(ValueError):
        resolve(rows, links)


def test_no_mutation_and_no_provider_io():
    record = fact()
    links = {"edition:paper": "link:v1"}
    before = record.copy(), links.copy()
    resolve([record], links)
    assert (record, links) == before
