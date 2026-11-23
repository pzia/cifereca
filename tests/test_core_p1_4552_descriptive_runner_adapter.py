"""#4552: verified runner results are proposed, never silently published."""
from dataclasses import replace

import pytest

from cifereca.core.notice_descriptive_runner_adapter import (
    propose_registered_descriptive_materialization,
)
from tests.test_core_p1_4981_consolidator_worker import (
    DECLARATION, OTHER, SCHEMA, TARGET, FOREIGN, fact, installed,
)


def propose(**changes):
    options = dict(
        declarations=(OTHER, DECLARATION), text_schemas=(SCHEMA,),
        installed=installed(), canonical_type="age", scope="fr:editions",
        selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
        target_edition_id=TARGET, source_facts=(fact(),),
        attested_strong_links={},
    )
    options.update(changes)
    return propose_registered_descriptive_materialization(**options)


def test_real_worker_proposes_versioned_exact_materialization():
    result = propose()
    assert result.materialization.value == "13+"
    assert result.materialization.inherited is False
    assert result.materialization.plugin_id == "plugin.age.alpha"
    assert result.materialization.source_evidence_keys == (("bnf", TARGET, "proof1"),)
    assert result.expected_execution_key == result.materialization.execution_key
    assert propose() == result


def test_strong_link_and_binary_change_affect_execution_identity():
    related = (fact("10+", edition=FOREIGN),)
    a = propose(source_facts=related, attested_strong_links={FOREIGN: "strong:a"})
    b = propose(source_facts=related, attested_strong_links={FOREIGN: "strong:b"})
    assert a.materialization.inherited is True
    assert a.expected_execution_key != b.expected_execution_key
    alternative = installed(source='def consolidate(facts):\n    return None\n')
    assert propose(installed=alternative) is None


def test_no_evidence_or_missing_selection_does_not_publish():
    assert propose(source_facts=()) is None
    assert propose(source_facts=(fact(edition=FOREIGN),)) is None
    with pytest.raises(ValueError, match="consolidator_not_selected"):
        propose(selected_version="0.0.0")


def test_unbounded_or_generator_snapshot_rejected():
    with pytest.raises(ValueError, match="invalid_descriptive_source_snapshot"):
        propose(source_facts=(fact() for _ in range(1)))
