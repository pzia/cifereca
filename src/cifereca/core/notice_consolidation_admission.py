"""#4981: one inert Core admission boundary for a proposed Notice C text result.

The caller supplies Core-vetted source evidence and installed declarations.
This never loads or executes a consolidator and never persists a notice.
"""
from __future__ import annotations

from .notice_consolidation_inputs import admit_descriptive_facts
from .notice_consolidator_selection import select_consolidator
from .notice_canonical_text_schema import (
    select_text_schema, validate_text_schema_result,
)
from .notice_consolidation_result_validation import validate_descriptive_result


def validate_registered_text_proposal(
    *, declarations: object, text_schemas: object,
    canonical_type: str, scope: str, plugin_id: str, version: str,
    target_edition_id: str, source_facts: object,
    attested_strong_links: object, proposed: object,
):
    """Apply ALL exact selection, source, result and text-schema gates.

    The installed registry and text schemas are validated even for an unknown
    output. No default plugin, missing schema fallback or cross-scope schema
    substitution is permitted. This is an admission boundary, not a runner.
    """
    selected = select_consolidator(
        declarations, canonical_type=canonical_type, scope=scope,
        selected_plugin_id=plugin_id, selected_version=version,
    )
    if selected is None:
        raise ValueError("consolidator_not_selected")
    schema = select_text_schema(text_schemas, selection=selected)
    if schema is None:
        raise ValueError("canonical_text_schema_not_installed")
    admitted = admit_descriptive_facts(
        target_edition_id=target_edition_id,
        canonical_type=canonical_type,
        consolidator_id=selected.plugin_id,
        consolidator_version=selected.version,
        source_facts=source_facts,
        attested_strong_links=attested_strong_links,
    )
    result = validate_descriptive_result(
        selection=selected, admitted=admitted, proposed=proposed,
    )
    if result is None:
        return None
    return validate_text_schema_result(
        result, schema=schema, selection=selected,
    )


__all__ = ["validate_registered_text_proposal"]
