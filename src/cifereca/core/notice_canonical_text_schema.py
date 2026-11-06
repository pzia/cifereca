"""#4552: Core-only validation of plugin-declared canonical TEXT schemas.

This pure, explicitly selected v1 contract covers bounded textual metadata.
It is NOT an all-type schema language, parser, reducer, plugin loader, cache,
source writer, rights checker or permission to copy between editions.
Future non-textual types require a separate declared/validated ABI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .notice_consolidator_selection import ExplicitSelection, _identifier
from .notice_consolidation_result_validation import ValidatedDescriptiveResult

MAX_DECLARATIONS = 128
MAX_TEXT_BYTES = 2048
MAX_ENUM_VALUES = 128


@dataclass(frozen=True, slots=True)
class CanonicalTextSchema:
    """One installed plugin declaration, not authority to execute the plugin."""

    canonical_type: str
    scope: str
    plugin_id: str
    plugin_version: str
    schema_id: str
    max_bytes: int
    inherited_allowed: bool
    allowed_values: tuple[str, ...] = ()


def _bounded_value(value: Any, limit: int) -> bool:
    # NUL is not descriptive text, even if UTF-8 size is within budget.
    # Reject it before a value reaches downstream C/GTK/SQL consumers.
    if type(value) is not str or "\x00" in value:
        return False
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    return 0 < size <= limit


def _valid_schema(row: object) -> bool:
    if type(row) is not CanonicalTextSchema:
        return False
    if (not _identifier(row.canonical_type)
            or not _identifier(row.scope, max_len=160)
            or not _identifier(row.plugin_id)
            or not _identifier(row.plugin_version)
            or not _identifier(row.schema_id)
            or type(row.max_bytes) is not int
            or not 1 <= row.max_bytes <= MAX_TEXT_BYTES
            or type(row.inherited_allowed) is not bool
            or type(row.allowed_values) is not tuple
            or len(row.allowed_values) > MAX_ENUM_VALUES):
        return False
    return (
        all(_bounded_value(v, row.max_bytes) for v in row.allowed_values)
        and len(set(row.allowed_values)) == len(row.allowed_values)
    )


def select_text_schema(
    declarations: object, *, selection: ExplicitSelection,
) -> CanonicalTextSchema | None:
    """Find exactly one selected schema, rejecting every malformed registry.

    Even one plausible declaration is not auto-selected: the prior Core
    ExplicitSelection pins type, scope, plugin, version and schema_id.
    Missing installed schema => unknown, never silently switch or downgrade.
    """
    if (type(selection) is not ExplicitSelection
            or not all(_identifier(v, max_len=160 if i == 1 else 128)
                       for i, v in enumerate((
                           selection.canonical_type, selection.scope,
                           selection.plugin_id, selection.version,
                           selection.schema_id,
                       )))
            or type(declarations) not in (tuple, list)
            or len(declarations) > MAX_DECLARATIONS):
        raise ValueError("invalid_canonical_text_schema_registry")
    seen: CanonicalTextSchema | None = None
    for row in declarations:
        if not _valid_schema(row):
            raise ValueError("invalid_canonical_text_schema")
        if (row.canonical_type, row.scope, row.plugin_id,
                row.plugin_version, row.schema_id) != (
                    selection.canonical_type, selection.scope,
                    selection.plugin_id, selection.version,
                    selection.schema_id):
            continue
        if seen is not None:
            raise ValueError("ambiguous_canonical_text_schema")
        seen = row
    return seen


def validate_text_schema_result(
    result: ValidatedDescriptiveResult, *, schema: CanonicalTextSchema,
    selection: ExplicitSelection,
) -> ValidatedDescriptiveResult:
    """Check bounded text and enum only AFTER descriptive provenance admission.

    The selection is compulsory: a schema from another scope could otherwise
    silently weaken inheritance or enum restrictions when the same plugin
    publishes the same schema_id for multiple scopes. The type-specific
    plugin still owns semantic interpretation; this gate grants no rights.
    """
    if (type(result) is not ValidatedDescriptiveResult
            or type(selection) is not ExplicitSelection
            or not _valid_schema(schema)):
        raise ValueError("invalid_canonical_text_validation_context")
    if (schema.canonical_type, schema.scope, schema.plugin_id,
            schema.plugin_version, schema.schema_id) != (
                selection.canonical_type, selection.scope,
                selection.plugin_id, selection.version, selection.schema_id,
            ):
        raise ValueError("canonical_text_schema_scope_mismatch")
    if (result.canonical_type, result.plugin_id, result.plugin_version,
            result.schema_id) != (selection.canonical_type, selection.plugin_id,
                                 selection.version, selection.schema_id):
        raise ValueError("canonical_text_schema_scope_mismatch")
    if not _bounded_value(result.value, schema.max_bytes):
        raise ValueError("canonical_text_schema_value_invalid")
    if schema.allowed_values and result.value not in schema.allowed_values:
        raise ValueError("canonical_text_schema_enum_mismatch")
    if result.inherited and not schema.inherited_allowed:
        raise ValueError("canonical_text_schema_inheritance_forbidden")
    return result


__all__ = [
    "CanonicalTextSchema", "select_text_schema", "validate_text_schema_result",
]
