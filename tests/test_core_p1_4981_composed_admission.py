"""#4981: compose public pure Notice C guards without plugin execution."""
from dataclasses import replace

import pytest

from cifereca.core.notice_consolidator_selection import ConsolidatorDeclaration
from cifereca.core.notice_canonical_text_schema import CanonicalTextSchema
from cifereca.core.notice_consolidation_admission import (
    validate_registered_text_proposal,
)

DECL = ConsolidatorDeclaration(
    plugin_id="consolidator.age", canonical_type="age",
    scope="edition:fr", version="1.0.0", schema_id="age.text.v1",
)
SCHEMA = CanonicalTextSchema(
    canonical_type=DECL.canonical_type, scope=DECL.scope,
    plugin_id=DECL.plugin_id, plugin_version=DECL.version,
    schema_id=DECL.schema_id, max_bytes=32, inherited_allowed=False,
    allowed_values=("9+", "13+"),
)
TARGET = "edition:1"
KEY = ("bnf", TARGET, "evidence:1")
FACTS = [{
    "source_id": KEY[0], "edition_id": TARGET, "evidence_id": KEY[2],
    "canonical_type": "age", "value": "13+", "source_revision": "revision:1",
}]
PROPOSED = {
    "canonical_type": "age", "target_edition_id": TARGET,
    "schema_id": DECL.schema_id, "value": "13+", "inherited": False,
    "source_evidence_keys": [KEY],
}


def invoke(**override):
    args = dict(
        declarations=[DECL], text_schemas=[SCHEMA],
        canonical_type="age", scope="edition:fr",
        plugin_id=DECL.plugin_id, version=DECL.version,
        target_edition_id=TARGET, source_facts=FACTS,
        attested_strong_links={}, proposed=PROPOSED,
    )
    args.update(override)
    return validate_registered_text_proposal(**args)


def test_complete_admission_preserves_exact_source_and_plugin_version():
    accepted = invoke()
    assert accepted.value == "13+"
    assert accepted.plugin_id == DECL.plugin_id
    assert accepted.plugin_version == DECL.version
    assert accepted.source_evidence_keys == (KEY,)
    assert invoke(proposed=None) is None


@pytest.mark.parametrize(("override", "reason"), [
    ({"plugin_id": "another.plugin"}, "consolidator_not_selected"),
    ({"version": "2.0.0"}, "consolidator_not_selected"),
    ({"scope": "edition:other"}, "consolidator_not_selected"),
    ({"text_schemas": []}, "canonical_text_schema_not_installed"),
    ({"text_schemas": [replace(SCHEMA, scope="edition:other")]},
     "canonical_text_schema_not_installed"),
    ({"proposed": PROPOSED | {"value": "other"}}, "canonical_text_schema_enum_mismatch"),
    ({"proposed": PROPOSED | {"target_edition_id": "edition:other"}},
     "consolidator_result_scope_mismatch"),
    ({"proposed": PROPOSED | {"source_evidence_keys": [("x", TARGET, "fake")]}},
     "unattested_result_references"),
    ({"source_facts": []}, "unattested_result_references"),
    ({"declarations": [DECL, DECL]}, "ambiguous_consolidator_declaration"),
    ({"text_schemas": [SCHEMA, SCHEMA]}, "ambiguous_canonical_text_schema"),
])
def test_no_implicit_fallback_or_forged_canonical_proof(override, reason):
    with pytest.raises(ValueError, match=reason):
        invoke(**override)


def test_unselected_duplicate_declaration_blocks_even_valid_proposal():
    other = replace(SCHEMA, canonical_type="different")
    with pytest.raises(ValueError, match="ambiguous_canonical_text_schema"):
        invoke(text_schemas=[SCHEMA, other, other])


def test_inherited_value_cannot_override_exact_source():
    related = {
        **FACTS[0], "edition_id": "edition:2",
        "evidence_id": "related:1", "value": "9+",
    }
    with pytest.raises(ValueError, match="exact_source_evidence_has_priority"):
        invoke(
            source_facts=FACTS + [related],
            attested_strong_links={"edition:2": "proof:1"},
            proposed=PROPOSED | {
                "inherited": True,
                "source_evidence_keys": [("bnf", "edition:2", "related:1")],
                "value": "9+",
            },
        )
