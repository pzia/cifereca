"""#4552: deterministic, revision-bound descriptive fingerprint, not rights cache."""
from dataclasses import replace

import pytest

from cifereca.core.notice_consolidation_inputs import admit_descriptive_facts
from cifereca.core.notice_consolidator_selection import ExplicitSelection
from cifereca.core.notice_consolidation_lineage import descriptive_lineage_key


SELECTED = ExplicitSelection(
    "age", "fr:edition", "consolidator.age", "1.0.0", "age.schema.v1",
)
TARGET = "isbn:9780306406157"
RELATED = "isbn:9780306406158"


def row(*, source="bnf", edition=TARGET, evidence="one", version="rev1",
        value="13+"):
    return {
        "source_id": source, "canonical_type": "age",
        "edition_id": edition, "evidence_id": evidence,
        "source_revision": version, "value": value,
    }


def admitted(rows, *, proof="strong:ark1"):
    return admit_descriptive_facts(
        target_edition_id=TARGET, canonical_type="age",
        consolidator_id=SELECTED.plugin_id,
        consolidator_version=SELECTED.version,
        source_facts=rows,
        attested_strong_links={RELATED: proof},
    )


def key(rows, *, proof="strong:ark1", selected=SELECTED):
    return descriptive_lineage_key(
        selection=selected, admitted=admitted(rows, proof=proof),
    )


def test_order_and_duplicate_proofs_do_not_change_revision_key():
    first = row(source="a", evidence="one")
    second = row(source="b", evidence="two")
    baseline = key([first, second])
    assert baseline == key([second, first, first])
    assert baseline.startswith("notice-c-desc-v1:")
    assert len(baseline) == len("notice-c-desc-v1:") + 64


@pytest.mark.parametrize("change", [
    {"version": "rev2"}, {"value": "12+"}, {"source": "other"},
    {"evidence": "different"}, {"edition": RELATED},
])
def test_every_changed_fact_dimension_invalidates_its_old_cache_key(change):
    assert key([row()]) != key([row(**change)])


def test_attested_strong_relation_proof_change_invalidates_cache():
    related = row(edition=RELATED)
    assert key([related], proof="proof:v1") != key([related], proof="proof:v2")


def test_exact_and_inherited_inputs_cannot_share_same_cache_key():
    assert key([row()]) != key([row(edition=RELATED)])


@pytest.mark.parametrize("change", [
    {"plugin_id": "other.plugin"},
    {"version": "1.0.1"},
    {"scope": "fr:other"},
    {"schema_id": "age.schema.v2"},
])
def test_selection_changes_invalidate_or_reject_key(change):
    selected = replace(SELECTED, **change)
    if "plugin_id" in change or "version" in change:
        with pytest.raises(ValueError, match="consolidator_context_mismatch"):
            key([row()], selected=selected)
    else:
        assert key([row()], selected=selected) != key([row()])


def test_implicit_metadata_never_leaks_into_key():
    raw = row()
    raw.update(account="secret", authorization="Bearer very-secret",
               loan_right=True)
    assert key([raw]) == key([row()])
    assert "Bearer" not in key([raw])
    assert "secret" not in key([raw])


def test_structured_json_prevents_delimiter_collision():
    a = row(source="a/b", evidence="c")
    b = row(source="a", evidence="b/c")
    assert key([a]) != key([b])


def test_context_and_bucket_tampering_are_rejected_before_hashing():
    valid = admitted([row()])
    with pytest.raises(ValueError):
        descriptive_lineage_key(
            selection=SELECTED,
            admitted=replace(valid, exact=(replace(valid.exact[0], inherited=True),)),
        )
    with pytest.raises(ValueError):
        descriptive_lineage_key(
            selection=SELECTED,
            admitted=replace(valid, exact=valid.exact * 513),
        )
    with pytest.raises(ValueError):
        descriptive_lineage_key(
            selection=SELECTED,
            admitted=replace(valid, inherited=valid.exact),
        )


@pytest.mark.parametrize("change", [
    {"source_id": "bad whitespace"},
    {"edition_id": "bad edition"},
    {"evidence_id": "bad evidence"},
    {"source_revision": "bad revision"},
    {"value": ""},
    {"value": "x" * 2049},
    {"value": "invalid\ud800"},
    {"value": None},
])
def test_directly_constructed_source_fact_cannot_bypass_admission(change):
    valid = admitted([row()])
    forged = replace(valid, exact=(replace(valid.exact[0], **change),))
    with pytest.raises(ValueError, match="invalid_lineage_fact"):
        descriptive_lineage_key(selection=SELECTED, admitted=forged)


def test_unverified_inherited_relation_proof_does_not_enter_lineage():
    valid = admitted([row(edition=RELATED)])
    forged = replace(valid, inherited=(
        replace(valid.inherited[0], relation_proof_id="bad proof"),
    ))
    with pytest.raises(ValueError, match="invalid_lineage_fact"):
        descriptive_lineage_key(selection=SELECTED, admitted=forged)


@pytest.mark.parametrize("scope", [
    {"target_edition_id": "bad edition"},
    {"consolidator_id": "bad plugin"},
])
def test_forged_admitted_scope_rejected_before_digest(scope):
    valid = admitted([row()])
    with pytest.raises(ValueError, match="invalid_lineage_scope"):
        descriptive_lineage_key(
            selection=SELECTED, admitted=replace(valid, **scope),
        )


def test_unvalidated_selected_schema_cannot_be_used_as_cache_key():
    with pytest.raises(ValueError, match="invalid_lineage_scope"):
        key([row()], selected=replace(SELECTED, schema_id="bad schema"))


@pytest.mark.parametrize("change", [
    {"exact": None},
    {"inherited": []},
    {"rejected_count": 513},
    {"rejected_count": True},
])
def test_invalid_lineage_bucket_shape_or_rejected_budget_fails_closed(change):
    with pytest.raises(ValueError, match="invalid_lineage_size"):
        descriptive_lineage_key(
            selection=SELECTED, admitted=replace(admitted([row()]), **change),
        )
