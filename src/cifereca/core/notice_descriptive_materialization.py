"""#4552: immutable, versioned Core descriptive materialization envelope.

Pure publication contract only. The caller MUST capture/recheck source
revisions, selection, installed executable and journal lease transactionally
before writing this envelope. No I/O, runner, rights or automatic publication.
"""
from __future__ import annotations

from dataclasses import dataclass

from .notice_consolidation_inputs import ConsolidationInputs
from .notice_consolidation_lineage import descriptive_execution_key
from .notice_consolidation_result_validation import (
    ValidatedDescriptiveResult, validate_descriptive_result,
)
from .notice_consolidator_selection import ExplicitSelection

MATERIALIZATION_VERSION = 1


@dataclass(frozen=True, slots=True)
class DescriptiveMaterialization:
    version: int
    execution_key: str
    canonical_type: str
    target_edition_id: str
    scope: str
    plugin_id: str
    plugin_version: str
    schema_id: str
    value: str
    inherited: bool
    source_evidence_keys: tuple[tuple[str, str, str], ...]


def prepare_descriptive_materialization(
    *, selection: ExplicitSelection, admitted: ConsolidationInputs,
    installed_source_sha256: str, result: ValidatedDescriptiveResult,
) -> DescriptiveMaterialization:
    """Bind a previously validated result to exact admitted evidence and code.

    A forged result is revalidated against the current admission; an old result
    cannot be republished after source revision or executable changes without
    an explicit expected-key comparison at the transactional writer.
    """
    key = descriptive_execution_key(
        selection=selection, admitted=admitted,
        installed_source_sha256=installed_source_sha256,
    )
    if type(result) is not ValidatedDescriptiveResult:
        raise ValueError("invalid_descriptive_materialization_result")
    # Never trust a public dataclass: reconstruct the proposed plugin ABI and
    # re-run the full provenance gate against current admitted evidence.
    checked = validate_descriptive_result(
        selection=selection, admitted=admitted, proposed={
            "canonical_type": result.canonical_type,
            "target_edition_id": result.target_edition_id,
            "schema_id": result.schema_id,
            "value": result.value,
            "inherited": result.inherited,
            "source_evidence_keys": result.source_evidence_keys,
        },
    )
    if (checked is None or type(result.plugin_id) is not str
            or type(result.plugin_version) is not str
            or result.plugin_id != selection.plugin_id
            or result.plugin_version != selection.version
            or checked != result):
        raise ValueError("descriptive_materialization_result_mismatch")
    return DescriptiveMaterialization(
        version=MATERIALIZATION_VERSION, execution_key=key,
        canonical_type=selection.canonical_type,
        target_edition_id=admitted.target_edition_id,
        scope=selection.scope, plugin_id=selection.plugin_id,
        plugin_version=selection.version, schema_id=selection.schema_id,
        value=checked.value, inherited=checked.inherited,
        source_evidence_keys=checked.source_evidence_keys,
    )


def require_current_descriptive_materialization(
    materialization: DescriptiveMaterialization, *,
    selection: ExplicitSelection, admitted: ConsolidationInputs,
    installed_source_sha256: str,
) -> DescriptiveMaterialization:
    """Read-side fail-closed revision gate; stale records must not be served."""
    if (type(materialization) is not DescriptiveMaterialization
            or type(materialization.version) is not int
            or materialization.version != MATERIALIZATION_VERSION):
        raise ValueError("invalid_descriptive_materialization")
    current = prepare_descriptive_materialization(
        selection=selection, admitted=admitted,
        installed_source_sha256=installed_source_sha256,
        result=ValidatedDescriptiveResult(
            canonical_type=materialization.canonical_type,
            target_edition_id=materialization.target_edition_id,
            plugin_id=materialization.plugin_id,
            plugin_version=materialization.plugin_version,
            schema_id=materialization.schema_id,
            value=materialization.value,
            inherited=materialization.inherited,
            source_evidence_keys=materialization.source_evidence_keys,
        ),
    )
    if current != materialization:
        raise ValueError("stale_descriptive_materialization")
    return materialization
