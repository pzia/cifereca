"""Trusted Core configuration owner for explicitly installed text consolidators.

Registry inputs must originate from a verified installed package inventory.
This module does not authenticate packages, load modules, execute plugins,
schedule work or grant source access. No implicit selection or fallback.
"""
from hashlib import sha256

from .notice_consolidator_selection import select_consolidator
from .notice_canonical_text_schema import select_text_schema, canonical_text_schema_digest
from .notice_consolidator_runner import InstalledTextConsolidator, MAX_SCRIPT_BYTES
from .notice_descriptive_selection_store import set_descriptive_selection


def configure_descriptive_selection(
    storage, *, declarations, text_schemas, installed_registry,
    canonical_type, scope, selected_plugin_id, selected_version,
    expected_generation,
):
    """Resolve a vetted registry and commit the exact selection by CAS.

    Explicit removal, or a configured version that is no longer installed,
    persists a tombstone. A malformed or ambiguous registry is refused
    without mutating the prior selection.
    """
    selection = select_consolidator(
        declarations, canonical_type=canonical_type, scope=scope,
        selected_plugin_id=selected_plugin_id, selected_version=selected_version,
    )
    if type(installed_registry) not in (tuple, list) or len(installed_registry) > 128:
        raise ValueError("invalid_installed_consolidator_registry")
    # Validate every installed binding, not just the selected candidate.
    bindings = {}
    for row in installed_registry:
        if type(row) is not InstalledTextConsolidator:
            raise ValueError("invalid_installed_consolidator_registry")
        fields = (row.canonical_type, row.scope, row.plugin_id, row.version,
                  row.schema_id, row.source, row.source_sha256)
        if any(type(value) is not str for value in fields):
            raise ValueError("invalid_installed_consolidator_registry")
        declared = select_consolidator(
            declarations, canonical_type=row.canonical_type, scope=row.scope,
            selected_plugin_id=row.plugin_id, selected_version=row.version,
        )
        if declared is None or declared.schema_id != row.schema_id:
            raise ValueError("installed_consolidator_mismatch")
        if select_text_schema(text_schemas, selection=declared) is None:
            raise ValueError("canonical_text_schema_not_installed")
        try:
            source = row.source.encode("utf-8")
        except UnicodeError:
            raise ValueError("installed_consolidator_mismatch") from None
        if not 0 < len(source) <= MAX_SCRIPT_BYTES or sha256(source).hexdigest() != row.source_sha256:
            raise ValueError("installed_consolidator_mismatch")
        if declared in bindings:
            raise ValueError("ambiguous_installed_consolidator")
        bindings[declared] = row
    if selection is not None:
        schema = select_text_schema(text_schemas, selection=selection)
        binding = bindings.get(selection)
        if schema is None or binding is None:
            selection = None
    else:
        binding = None
    return set_descriptive_selection(
        storage, canonical_type=canonical_type, scope=scope,
        selection=selection,
        installed_source_sha256=None if selection is None else binding.source_sha256,
        expected_generation=expected_generation,
        schema_sha256=None if selection is None else canonical_text_schema_digest(schema),
    )
