"""#4552: descriptive materialization is bound to evidence and executable."""
from dataclasses import replace

import pytest

from cifereca.core.notice_consolidation_inputs import admit_descriptive_facts
from cifereca.core.notice_consolidation_result_validation import (
    validate_descriptive_result,
)
from cifereca.core.notice_consolidator_selection import ExplicitSelection
from cifereca.core.notice_descriptive_materialization import (
    prepare_descriptive_materialization,
    require_current_descriptive_materialization,
)

SELECTION = ExplicitSelection("age", "edition:fr", "plugin.age", "1.0", "age.v1")
SOURCE = "a" * 64
TARGET = "edition:1"


def admitted(*, revision="rev:1", proof="strong:1", value="adult"):
    return admit_descriptive_facts(
        target_edition_id=TARGET, canonical_type="age",
        consolidator_id="plugin.age", consolidator_version="1.0",
        source_facts=[{
            "source_id": "bnf", "edition_id": "edition:2",
            "evidence_id": "evidence:1", "source_revision": revision,
            "canonical_type": "age", "value": value,
        }],
        attested_strong_links={"edition:2": proof},
    )


def result(facts):
    return validate_descriptive_result(
        selection=SELECTION, admitted=facts, proposed={
            "canonical_type": "age", "target_edition_id": TARGET,
            "schema_id": "age.v1", "value": "adult", "inherited": True,
            "source_evidence_keys": [["bnf", "edition:2", "evidence:1"]],
        },
    )


def materialize(facts, *, digest=SOURCE):
    return prepare_descriptive_materialization(
        selection=SELECTION, admitted=facts,
        installed_source_sha256=digest, result=result(facts),
    )


def test_versioned_materialization_replay_same_inputs_is_stable():
    facts = admitted()
    first = materialize(facts)
    assert first.version == 1
    assert first.inherited is True
    assert materialize(facts) == first
    assert require_current_descriptive_materialization(
        first, selection=SELECTION, admitted=facts,
        installed_source_sha256=SOURCE,
    ) is first


@pytest.mark.parametrize("change", [
    {"revision": "rev:2"}, {"proof": "strong:2"}, {"value": "child"},
])
def test_source_or_relation_change_invalidates_read(change):
    facts = admitted()
    old = materialize(facts)
    with pytest.raises(ValueError, match="stale_descriptive_materialization"):
        require_current_descriptive_materialization(
            old, selection=SELECTION, admitted=admitted(**change),
            installed_source_sha256=SOURCE,
        )


def test_installed_binary_digest_change_invalidates_read():
    old = materialize(admitted())
    with pytest.raises(ValueError, match="stale_descriptive_materialization"):
        require_current_descriptive_materialization(
            old, selection=SELECTION, admitted=admitted(),
            installed_source_sha256="b" * 64,
        )


def test_forged_result_or_materialization_fails_closed():
    facts = admitted()
    old = materialize(facts)
    with pytest.raises(ValueError, match="descriptive_materialization_result_mismatch"):
        prepare_descriptive_materialization(
            selection=SELECTION, admitted=facts,
            installed_source_sha256=SOURCE,
            result=replace(result(facts), plugin_id="other"),
        )
    with pytest.raises(ValueError, match="stale_descriptive_materialization"):
        require_current_descriptive_materialization(
            replace(old, execution_key="notice-c-exec-v1:" + "0" * 64), selection=SELECTION,
            admitted=facts, installed_source_sha256=SOURCE,
        )
    with pytest.raises(ValueError, match="invalid_descriptive_materialization"):
        require_current_descriptive_materialization(
            replace(old, version=True), selection=SELECTION,
            admitted=facts, installed_source_sha256=SOURCE,
        )
