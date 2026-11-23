"""#4552: compose the real public Search C source inventory with Core jobs.

The authoritative sources are captured from SQLite on both execution and
publication. No provider, network, UI or plugin runs inside that transaction.
"""
from __future__ import annotations

from .notice_c_public_age_sql_snapshot import capture_public_age_sources_txn
from .notice_descriptive_core_coordinator import run_descriptive_core_job


def run_public_age_descriptive_job(
    storage, *, source_documents, declarations, text_schemas, installed,
    scope, selected_plugin_id, selected_version, target_edition_id,
    now, lease_seconds, expected_generation=0, expected_execution_key=None,
):
    """Run one exact-edition public age consolidation through durable Core."""
    documents = tuple(source_documents)
    if not documents:
        return None

    def capture(db):
        return capture_public_age_sources_txn(
            db, documents=documents, target_edition_id=target_edition_id,
        )

    return run_descriptive_core_job(
        storage, capture_sources_txn=capture,
        declarations=declarations, text_schemas=text_schemas,
        installed=installed, canonical_type="age", scope=scope,
        selected_plugin_id=selected_plugin_id,
        selected_version=selected_version,
        target_edition_id=target_edition_id, now=now,
        lease_seconds=lease_seconds,
        expected_generation=expected_generation,
        expected_execution_key=expected_execution_key,
    )
