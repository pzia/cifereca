"""#4552: SQL materialization CAS and restart/read-side invalidation."""
from dataclasses import replace

import pytest

from cifereca.storage import Storage
from cifereca.core.notice_consolidation_inputs import admit_descriptive_facts
from cifereca.core.notice_consolidation_result_validation import validate_descriptive_result
from cifereca.core.notice_consolidator_selection import ExplicitSelection
from cifereca.core.notice_descriptive_materialization import prepare_descriptive_materialization
from cifereca.core.notice_descriptive_materialization_store import (
    publish_descriptive_materialization, read_descriptive_materialization,
)

SELECTION = ExplicitSelection("age", "edition:fr", "plugin.age", "1.0", "age.v1")
SOURCE = "a" * 64


def facts(revision="rev:1", proof="proof:1"):
    return admit_descriptive_facts(
        target_edition_id="edition:1", canonical_type="age",
        consolidator_id="plugin.age", consolidator_version="1.0",
        source_facts=[{
            "source_id": "bnf", "edition_id": "edition:2",
            "evidence_id": "evidence:1", "source_revision": revision,
            "canonical_type": "age", "value": "adult",
        }], attested_strong_links={"edition:2": proof},
    )


def materialization(admitted):
    checked = validate_descriptive_result(
        selection=SELECTION, admitted=admitted, proposed={
            "canonical_type": "age", "target_edition_id": "edition:1",
            "schema_id": "age.v1", "value": "adult", "inherited": True,
            "source_evidence_keys": [["bnf", "edition:2", "evidence:1"]],
        },
    )
    return prepare_descriptive_materialization(
        selection=SELECTION, admitted=admitted,
        installed_source_sha256=SOURCE, result=checked,
    )


def read(storage, capture):
    return read_descriptive_materialization(
        storage, target_edition_id="edition:1",
        canonical_type="age", scope="edition:fr",
        capture_current_txn=capture,
    )


def test_sql_roundtrip_restart_and_cas(tmp_path):
    path = tmp_path / "desc.sqlite"
    store = Storage(path)
    current = facts()
    row = materialization(current)
    capture = lambda db: (SELECTION, current, SOURCE)
    assert read(store, capture) is None
    publish_descriptive_materialization(
        store, row, expected_execution_key=None, capture_current_txn=capture,
    )
    assert read(Storage(path), capture) == row
    with pytest.raises(ValueError, match="descriptive_materialization_cas_changed"):
        publish_descriptive_materialization(
            store, row, expected_execution_key=None, capture_current_txn=capture,
        )
    assert publish_descriptive_materialization(
        store, row, expected_execution_key=row.execution_key,
        capture_current_txn=capture,
    ) == row


@pytest.mark.parametrize("change", [
    {"revision": "rev:2"}, {"proof": "proof:2"},
])
def test_snapshot_change_prevents_publication_and_stale_read(tmp_path, change):
    store = Storage(tmp_path / "desc.sqlite")
    original = facts()
    updated = facts(**change)
    row = materialization(original)
    with pytest.raises(ValueError, match="stale_descriptive_materialization"):
        publish_descriptive_materialization(
            store, row, expected_execution_key=None,
            capture_current_txn=lambda db: (SELECTION, updated, SOURCE),
        )
    assert read(store, lambda db: (SELECTION, original, SOURCE)) is None
    publish_descriptive_materialization(
        store, row, expected_execution_key=None,
        capture_current_txn=lambda db: (SELECTION, original, SOURCE),
    )
    with pytest.raises(ValueError, match="stale_descriptive_materialization"):
        read(store, lambda db: (SELECTION, updated, SOURCE))


def test_executable_change_and_corrupt_key_fail_closed(tmp_path):
    store = Storage(tmp_path / "desc.sqlite")
    admitted = facts()
    row = materialization(admitted)
    capture = lambda db: (SELECTION, admitted, SOURCE)
    publish_descriptive_materialization(
        store, row, expected_execution_key=None, capture_current_txn=capture,
    )
    with pytest.raises(ValueError, match="stale_descriptive_materialization"):
        read(store, lambda db: (SELECTION, admitted, "b" * 64))
    with pytest.raises(ValueError, match="stale_descriptive_materialization"):
        publish_descriptive_materialization(
            store, replace(row, execution_key="notice-c-exec-v1:" + "0" * 64),
            expected_execution_key=row.execution_key, capture_current_txn=capture,
        )
