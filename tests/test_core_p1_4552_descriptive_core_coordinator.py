"""#4552: Core coordinator real worker, journal and durable SQL integration."""
from hashlib import sha256

import pytest

from cifereca.storage import Storage
from cifereca.core.notice_descriptive_core_coordinator import run_descriptive_core_job
from cifereca.core.notice_descriptive_materialization_store import read_descriptive_materialization
from cifereca.core.notice_consolidation_inputs import admit_descriptive_facts
from cifereca.core.notice_consolidator_selection import select_consolidator
from tests.test_core_p1_4981_consolidator_worker import (
    DECLARATION, SCHEMA, TARGET, fact, installed,
)


def test_real_worker_journal_sql_restart_and_replay(tmp_path):
    storage = Storage(tmp_path / "core.sqlite")
    installed_binding = installed()
    def sources(db):
        return (fact(),), {}
    options = dict(
        capture_sources_txn=sources, declarations=(DECLARATION,),
        text_schemas=(SCHEMA,), installed=installed_binding,
        canonical_type="age", scope="fr:editions",
        selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
        target_edition_id=TARGET, now=100, lease_seconds=60,
    )
    row = run_descriptive_core_job(storage, **options)
    assert row.value == "13+"
    selection = select_consolidator(
        (DECLARATION,), canonical_type="age", scope="fr:editions",
        selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
    )
    admitted = admit_descriptive_facts(
        target_edition_id=TARGET, canonical_type="age",
        consolidator_id=selection.plugin_id,
        consolidator_version=selection.version,
        source_facts=(fact(),), attested_strong_links={},
    )
    assert read_descriptive_materialization(
        Storage(tmp_path / "core.sqlite"),
        target_edition_id=TARGET, canonical_type="age",
        scope="fr:editions",
        capture_current_txn=lambda db: (
            selection, admitted, installed_binding.source_sha256,
        ),
    ) == row
    # The same completed generation cannot be claimed or published again.
    assert run_descriptive_core_job(storage, **options) is None


def test_no_admitted_evidence_does_not_create_work(tmp_path):
    storage = Storage(tmp_path / "empty.sqlite")
    assert run_descriptive_core_job(
        storage, capture_sources_txn=lambda db: ((), {}),
        declarations=(DECLARATION,), text_schemas=(SCHEMA,),
        installed=installed(), canonical_type="age", scope="fr:editions",
        selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
        target_edition_id=TARGET, now=100, lease_seconds=60,
    ) is None
