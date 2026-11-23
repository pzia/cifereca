"""#4552: Core adapter from verified text runner to immutable publication proposal.

The caller supplies installed registry data and Core-admitted observations.
No database writes, network, scheduler or rights decisions are performed here.
The proposal is not current until SQL CAS and journal fencing both succeed.
"""
from __future__ import annotations

from dataclasses import dataclass

from .notice_consolidation_inputs import admit_descriptive_facts
from .notice_consolidator_runner import (
    InstalledTextConsolidator, run_registered_text_consolidator,
)
from .notice_consolidator_selection import select_consolidator
from .notice_descriptive_materialization import (
    DescriptiveMaterialization, prepare_descriptive_materialization,
)


@dataclass(frozen=True, slots=True)
class DescriptivePublicationProposal:
    materialization: DescriptiveMaterialization
    expected_execution_key: str


def propose_registered_descriptive_materialization(
    *, declarations: object, text_schemas: object,
    installed: InstalledTextConsolidator,
    canonical_type: str, scope: str,
    selected_plugin_id: str, selected_version: str,
    target_edition_id: str, source_facts: object,
    attested_strong_links: object,
) -> DescriptivePublicationProposal | None:
    """Run vetted isolated plugin and bind its result to the same admission.

    This adapter never invents a selection or silently falls back to a
    different plugin/version. No result means no materialization and no
    successful journal completion.
    """
    selection = select_consolidator(
        declarations, canonical_type=canonical_type, scope=scope,
        selected_plugin_id=selected_plugin_id,
        selected_version=selected_version,
    )
    if selection is None:
        raise ValueError("consolidator_not_selected")
    # Materialize an immutable snapshot so runner and publication proposal
    # cannot silently observe different generator contents.
    if type(source_facts) not in (list, tuple):
        raise ValueError("invalid_descriptive_source_snapshot")
    snapshot = tuple(source_facts)
    admitted = admit_descriptive_facts(
        target_edition_id=target_edition_id, canonical_type=canonical_type,
        consolidator_id=selection.plugin_id,
        consolidator_version=selection.version,
        source_facts=snapshot, attested_strong_links=attested_strong_links,
    )
    result = run_registered_text_consolidator(
        declarations=declarations, text_schemas=text_schemas,
        installed=installed, canonical_type=canonical_type, scope=scope,
        selected_plugin_id=selected_plugin_id,
        selected_version=selected_version,
        target_edition_id=target_edition_id, source_facts=snapshot,
        attested_strong_links=attested_strong_links,
    )
    if result is None:
        return None
    materialization = prepare_descriptive_materialization(
        selection=selection, admitted=admitted,
        installed_source_sha256=installed.source_sha256, result=result,
    )
    return DescriptivePublicationProposal(
        materialization=materialization,
        expected_execution_key=materialization.execution_key,
    )
