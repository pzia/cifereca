"""#4552: Core checks plugin output *without executing* plugin code."""
from dataclasses import replace

import pytest

from cifereca.core.notice_consolidation_inputs import admit_descriptive_facts
from cifereca.core.notice_consolidator_selection import ExplicitSelection
from cifereca.core.notice_consolidation_result_validation import (
    ValidatedDescriptiveResult, validate_descriptive_result,
)

SELECTION = ExplicitSelection(
    canonical_type="age", scope="fr:editions",
    plugin_id="consolidator.age", version="1.0.0", schema_id="schema.age.v1",
)
TARGET = "isbn:9780306406157"
EXACT = ("bnf", TARGET, "fact1")
LINKED = ("bnf", "isbn:9780306406158", "fact2")


def context(*, with_exact=True, with_related=True):
    rows = []
    if with_exact:
        rows.append(dict(zip(
            ("source_id", "edition_id", "evidence_id"),
            EXACT,
        ), canonical_type="age", value="13+", source_revision="rev:1"))
    if with_related:
        rows.append(dict(zip(
            ("source_id", "edition_id", "evidence_id"),
            LINKED,
        ), canonical_type="age", value="10+", source_revision="rev:2"))
    return admit_descriptive_facts(
        target_edition_id=TARGET, canonical_type="age",
        consolidator_id=SELECTION.plugin_id,
        consolidator_version=SELECTION.version,
        source_facts=rows,
        attested_strong_links={LINKED[1]: "strong:v1"},
    )


def result(*, refs=(EXACT,), inherited=False, **kwargs):
    return {
        "canonical_type": "age", "target_edition_id": TARGET,
        "schema_id": "schema.age.v1", "value": "13+",
        "inherited": inherited, "source_evidence_keys": list(refs),
        **kwargs,
    }


def validate(proposed, *, admitted=None, selection=SELECTION):
    return validate_descriptive_result(
        selection=selection, admitted=admitted or context(),
        proposed=proposed,
    )


def test_exact_output_bound_to_selection_and_exact_edition():
    checked = validate(result())
    assert checked == ValidatedDescriptiveResult(
        "age", TARGET, "consolidator.age", "1.0.0",
        "schema.age.v1", "13+", False, (EXACT,),
    )
    assert validate(None) is None


def test_attested_inheritance_when_no_exact_evidence_is_explicitly_marked():
    checked = validate(
        result(refs=(LINKED,), inherited=True),
        admitted=context(with_exact=False),
    )
    assert checked.inherited is True
    assert checked.source_evidence_keys == (LINKED,)


@pytest.mark.parametrize("proposed", [
    result(refs=(LINKED,), inherited=True),
    result(refs=(LINKED,), inherited=False),
    result(refs=(EXACT,), inherited=True),
])
def test_cannot_use_related_evidence_as_exact_or_displace_exact(proposed):
    with pytest.raises(ValueError):
        validate(proposed)


@pytest.mark.parametrize("change", [
    {"canonical_type": "genre"},
    {"target_edition_id": "isbn:other"},
    {"schema_id": "schema.age.v2"},
    {"value": ""},
    {"value": "x" * 2049},
    {"value": chr(0xD800)},
    {"value": {"age": 13}},
    {"inherited": 1},
    {"source_evidence_keys": []},
    {"source_evidence_keys": [EXACT] * 129},
    {"source_evidence_keys": [EXACT, EXACT]},
    {"source_evidence_keys": [["bnf", "isbn:other", "fact1"]]},
    {"source_evidence_keys": [["bnf", TARGET, "other-proof"]]},
    {"source_evidence_keys": ["bnf/" + TARGET + "/fact1"]},
    {"source_evidence_keys": [123]},
])
def test_invalid_output_never_becomes_attested_result(change):
    with pytest.raises(ValueError):
        validate(result() | change)


@pytest.mark.parametrize("forbidden", [
    "account", "rights", "token", "offer", "borrower_identity_id",
    "credential_id", "download_url", "permissions", "license",
])
def test_extra_sensitive_fields_fail_closed(forbidden):
    with pytest.raises(ValueError, match="invalid_consolidator_result_shape"):
        validate(result() | {forbidden: "secret"})


def test_versioned_plugin_binding_cannot_cross_scope_or_version():
    incorrect = ExplicitSelection(
        "age", "fr:editions", "another.reducer", "1.0.0", "schema.age.v1",
    )
    with pytest.raises(ValueError, match="consolidator_context_mismatch"):
        validate(result(), selection=incorrect)


def test_evidence_references_are_structured_to_avoid_delimiter_collision():
    admitted = admit_descriptive_facts(
        target_edition_id=TARGET, canonical_type="age",
        consolidator_id=SELECTION.plugin_id,
        consolidator_version=SELECTION.version,
        source_facts=[{
            "source_id": "bnf/x", "edition_id": TARGET,
            "evidence_id": "proof", "canonical_type": "age",
            "value": "13+", "source_revision": "rev:1",
        }],
        attested_strong_links={},
    )
    invalid = result(refs=(("bnf", "x/" + TARGET, "proof"),))
    with pytest.raises(ValueError, match="unattested_result_references"):
        validate(invalid, admitted=admitted)


def test_reference_order_is_normalized_without_promoting_other_source():
    records = [dict(
        source_id=source, edition_id=TARGET, evidence_id=evidence,
        canonical_type="age", value="13+", source_revision="rev:1",
    ) for source, evidence in (("z", "z1"), ("a", "a1"))]
    admitted = admit_descriptive_facts(
        target_edition_id=TARGET, canonical_type="age",
        consolidator_id=SELECTION.plugin_id,
        consolidator_version=SELECTION.version,
        source_facts=records, attested_strong_links={},
    )
    refs = (("z", TARGET, "z1"), ("a", TARGET, "a1"))
    expected = tuple(sorted(refs))
    assert validate(result(refs=refs), admitted=admitted).source_evidence_keys == expected
    assert validate(result(refs=refs[::-1]), admitted=admitted).source_evidence_keys == expected


def test_forged_exact_bucket_cannot_attest_a_foreign_edition():
    admitted = context(with_related=False)
    foreign = replace(admitted.exact[0], edition_id="isbn:foreign-edition")
    forged = replace(admitted, exact=(foreign,))
    # The result references the forged DTO's key: membership alone used to
    # accept it even though the fact is not exact for the target edition.
    proposed = result(refs=((foreign.source_id, foreign.edition_id, foreign.evidence_id),))
    with pytest.raises(ValueError, match="invalid_lineage_fact"):
        validate(proposed, admitted=forged)


def test_forged_inherited_bucket_without_strong_proof_is_rejected():
    admitted = context(with_exact=False)
    forged_fact = replace(admitted.inherited[0], relation_proof_id=None)
    forged = replace(admitted, inherited=(forged_fact,))
    with pytest.raises(ValueError, match="invalid_lineage_fact"):
        validate(result(refs=(LINKED,), inherited=True), admitted=forged)


def test_empty_output_does_not_bypass_invalid_admitted_lineage():
    admitted = context(with_related=False)
    forged = replace(admitted, exact=(replace(admitted.exact[0], inherited=True),))
    with pytest.raises(ValueError, match="invalid_lineage_fact"):
        validate(None, admitted=forged)
