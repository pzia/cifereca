"""#4552: plugin-declared canonical text rules remain Core-validated and inert."""
from dataclasses import replace

import pytest

from cifereca.core.notice_consolidator_selection import ExplicitSelection
from cifereca.core.notice_consolidation_inputs import admit_descriptive_facts
from cifereca.core.notice_consolidation_result_validation import (
    ValidatedDescriptiveResult, validate_descriptive_result,
)
from cifereca.core.notice_canonical_text_schema import (
    CanonicalTextSchema, select_text_schema, validate_text_schema_result,
)


SELECTED = ExplicitSelection(
    canonical_type="custom.reading-level", scope="edition:fr",
    plugin_id="org.example.reader-level", version="1.2.3",
    schema_id="schema.reader-level.v1",
)
SCHEMA = CanonicalTextSchema(
    canonical_type=SELECTED.canonical_type, scope=SELECTED.scope,
    plugin_id=SELECTED.plugin_id, plugin_version=SELECTED.version,
    schema_id=SELECTED.schema_id, max_bytes=64, inherited_allowed=False,
    allowed_values=("novice", "regular", "advanced"),
)
RESULT = ValidatedDescriptiveResult(
    canonical_type=SELECTED.canonical_type, target_edition_id="book:01",
    plugin_id=SELECTED.plugin_id, plugin_version=SELECTED.version,
    schema_id=SELECTED.schema_id, value="regular", inherited=False,
    source_evidence_keys=(("catalog-a", "book:01", "evidence:01"),),
)


def test_explicit_registered_custom_type_without_core_type_whitelist():
    other = replace(SCHEMA, canonical_type="custom.secondary")
    assert select_text_schema([other, SCHEMA], selection=SELECTED) == SCHEMA
    assert validate_text_schema_result(RESULT, schema=SCHEMA) is RESULT
    assert select_text_schema([other], selection=SELECTED) is None


@pytest.mark.parametrize("change", [
    {"canonical_type": "custom.other"},
    {"scope": "edition:other"},
    {"plugin_id": "org.other"},
    {"plugin_version": "2.0.0"},
    {"schema_id": "schema.reader-level.v2"},
])
def test_never_fallback_to_other_type_scope_plugin_or_version(change):
    candidate = replace(SCHEMA, **change)
    assert select_text_schema([candidate], selection=SELECTED) is None


def test_identical_or_conflicting_duplicate_declaration_fails_closed():
    with pytest.raises(ValueError, match="ambiguous_canonical_text_schema"):
        select_text_schema([SCHEMA, SCHEMA], selection=SELECTED)
    with pytest.raises(ValueError, match="ambiguous_canonical_text_schema"):
        select_text_schema([SCHEMA, replace(SCHEMA, max_bytes=32)],
                           selection=SELECTED)


@pytest.mark.parametrize("change", [
    {"max_bytes": True}, {"max_bytes": 0}, {"max_bytes": 2049},
    {"inherited_allowed": 1}, {"allowed_values": ["regular"]},
    {"allowed_values": ("regular", "regular")},
    {"allowed_values": ("",)},
    {"allowed_values": ("bad\ud800",)},
    {"plugin_id": "secret credential"},
])
def test_bad_declaration_anywhere_invalidates_registry(change):
    malformed = replace(SCHEMA, **change)
    with pytest.raises(ValueError, match="invalid_canonical_text_schema"):
        select_text_schema([SCHEMA, malformed], selection=SELECTED)


def test_invalid_registry_length_or_shape_never_admits_a_plugin():
    with pytest.raises(ValueError, match="invalid_canonical_text_schema_registry"):
        select_text_schema([SCHEMA] * 129, selection=SELECTED)
    with pytest.raises(ValueError, match="invalid_canonical_text_schema_registry"):
        select_text_schema({"one": SCHEMA}, selection=SELECTED)


@pytest.mark.parametrize(("value", "message"), [
    ("other", "canonical_text_schema_enum_mismatch"),
    ("x" * 65, "canonical_text_schema_value_invalid"),
    ("", "canonical_text_schema_value_invalid"),
    ("bad\ud800", "canonical_text_schema_value_invalid"),
])
def test_value_is_bounded_and_checked_against_declared_enum(value, message):
    with pytest.raises(ValueError, match=message):
        validate_text_schema_result(replace(RESULT, value=value), schema=SCHEMA)


def test_inheritance_declarative_flag_is_only_a_restriction_not_permission():
    with pytest.raises(ValueError, match="canonical_text_schema_inheritance_forbidden"):
        validate_text_schema_result(replace(RESULT, inherited=True), schema=SCHEMA)
    permissive = replace(SCHEMA, inherited_allowed=True)
    assert validate_text_schema_result(
        replace(RESULT, inherited=True), schema=permissive
    ).inherited is True


def test_different_output_plugin_schema_or_type_is_not_rebound():
    for field, value in (
        ("canonical_type", "custom.other"),
        ("plugin_id", "org.other"),
        ("plugin_version", "2.0.0"),
        ("schema_id", "schema.reader-level.v2"),
    ):
        with pytest.raises(ValueError, match="canonical_text_schema_scope_mismatch"):
            validate_text_schema_result(
                replace(RESULT, **{field: value}), schema=SCHEMA,
            )


def test_schema_gate_composes_with_previous_core_evidence_and_result_gates():
    admitted = admit_descriptive_facts(
        target_edition_id="book:01",
        canonical_type=SELECTED.canonical_type,
        consolidator_id=SELECTED.plugin_id,
        consolidator_version=SELECTED.version,
        source_facts=[{
            "source_id": "catalog-a", "edition_id": "book:01",
            "evidence_id": "evidence:01", "source_revision": "rev:01",
            "canonical_type": SELECTED.canonical_type, "value": "regular",
        }],
        attested_strong_links={},
    )
    proposed = {
        "canonical_type": SELECTED.canonical_type,
        "target_edition_id": "book:01",
        "schema_id": SELECTED.schema_id,
        "value": "regular", "inherited": False,
        "source_evidence_keys": [["catalog-a", "book:01", "evidence:01"]],
    }
    checked = validate_descriptive_result(
        selection=SELECTED, admitted=admitted, proposed=proposed,
    )
    assert checked is not None
    assert validate_text_schema_result(
        checked, schema=select_text_schema([SCHEMA], selection=SELECTED),
    ) == checked
