"""#4552: fail-closed contract for a *proposed* descriptive consolidation.

A Core-validated ExplicitSelection and admitted ConsolidationInputs are
required. This module is pure: no runner, plugin invocation, IO or persistence.
The actual plugin output schema must independently be checked by the ABI
runner before calling this common provenance/identity gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .notice_consolidation_inputs import ConsolidationInputs, SourceFact
from .notice_consolidator_selection import ExplicitSelection
from .notice_consolidation_lineage import descriptive_lineage_key

MAX_CANONICAL_BYTES = 2048
MAX_REFERENCES = 128
_RESULT_KEYS = frozenset({
    "canonical_type", "target_edition_id", "schema_id", "value",
    "inherited", "source_evidence_keys",
})


@dataclass(frozen=True, slots=True)
class ValidatedDescriptiveResult:
    canonical_type: str
    target_edition_id: str
    plugin_id: str
    plugin_version: str
    schema_id: str
    value: str
    inherited: bool
    source_evidence_keys: tuple[tuple[str, str, str], ...]


def validate_descriptive_result(
    *, selection: ExplicitSelection,
    admitted: ConsolidationInputs,
    proposed: object,
) -> ValidatedDescriptiveResult | None:
    """Validate identity/provenance of one explicit plugin result.

    None is an intentional no-result (unknown). Reject otherwise on any
    mismatch or ambiguous evidence key. All evidence identifiers refer to
    the selected exact or strongly-linked bucket, NEVER to another edition.
    Inheritance requires no exact facts: exact descriptive evidence wins.
    This step does not normalize a value or grant a borrowing right.
    """
    if type(selection) is not ExplicitSelection or type(admitted) is not ConsolidationInputs:
        raise ValueError("invalid_consolidation_context")
    if (selection.canonical_type != admitted.canonical_type
            or selection.plugin_id != admitted.consolidator_id
            or selection.version != admitted.consolidator_version):
        raise ValueError("consolidator_context_mismatch")
    # DTOs are public Python objects, not authenticated admissions. Reuse the
    # bounded lineage validator before trusting any source evidence references.
    # This rejects forged exact/linked buckets even if their keys match the
    # plugin's proposed output. No cache or plugin execution is performed.
    descriptive_lineage_key(selection=selection, admitted=admitted)
    if proposed is None:
        return None
    if type(proposed) is not dict or frozenset(proposed) != _RESULT_KEYS:
        raise ValueError("invalid_consolidator_result_shape")
    if (type(proposed["canonical_type"]) is not str
            or proposed["canonical_type"] != selection.canonical_type
            or type(proposed["target_edition_id"]) is not str
            or proposed["target_edition_id"] != admitted.target_edition_id
            or type(proposed["schema_id"]) is not str
            or proposed["schema_id"] != selection.schema_id):
        raise ValueError("consolidator_result_scope_mismatch")
    inherited = proposed["inherited"]
    if type(inherited) is not bool:
        raise ValueError("invalid_result_inheritance")
    if inherited and admitted.exact:
        raise ValueError("exact_source_evidence_has_priority")
    value = proposed["value"]
    if type(value) is not str:
        raise ValueError("invalid_result_value")
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("invalid_result_value") from exc
    if not 0 < encoded_size <= MAX_CANONICAL_BYTES:
        raise ValueError("invalid_result_value")
    refs = proposed["source_evidence_keys"]
    if (type(refs) not in (list, tuple)
            or not 1 <= len(refs) <= MAX_REFERENCES
            or any(type(ref) not in (tuple, list) or len(ref) != 3
                   or any(type(field) is not str for field in ref)
                   for ref in refs)):
        raise ValueError("invalid_result_references")
    # Keep the triple structured: a delimiter-joined string would let
    # different editions/providers collide when identifiers contain '/'.
    # Only the same exact/relation-vetted evidence bucket can be referenced.
    keys = tuple(tuple(ref) for ref in refs)
    eligible = admitted.inherited if inherited else admitted.exact
    by_key: set[tuple[str, str, str]] = set()
    for fact in eligible:
        if type(fact) is not SourceFact:
            raise ValueError("invalid_admitted_evidence")
        key = (fact.source_id, fact.edition_id, fact.evidence_id)
        if key in by_key:
            raise ValueError("ambiguous_admitted_evidence")
        by_key.add(key)
    if len(set(keys)) != len(keys) or any(ref not in by_key for ref in keys):
        raise ValueError("unattested_result_references")
    return ValidatedDescriptiveResult(
        canonical_type=selection.canonical_type,
        target_edition_id=admitted.target_edition_id,
        plugin_id=selection.plugin_id,
        plugin_version=selection.version,
        schema_id=selection.schema_id,
        value=value, inherited=inherited,
        source_evidence_keys=tuple(sorted(keys)),
    )
