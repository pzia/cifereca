"""#4552: durable selection scope, revocation and ABA during isolated work."""
from dataclasses import replace

import pytest

from cifereca.storage import Storage
from cifereca.core import notice_descriptive_core_coordinator as coordinator
from cifereca.core.notice_consolidation_inputs import admit_descriptive_facts
from cifereca.core.notice_consolidator_selection import select_consolidator
from cifereca.core.notice_descriptive_materialization_store import read_descriptive_materialization
from cifereca.core.notice_descriptive_selection_store import (
    read_descriptive_selection_txn, set_descriptive_selection,
)
from tests.test_core_p1_4981_consolidator_worker import (
    DECLARATION, SCHEMA, TARGET, fact, installed,
)

SCOPE = dict(canonical_type="age", scope="fr:editions")


def selection():
    return select_consolidator(
        (DECLARATION,), **SCOPE,
        selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
    )


def put(storage, selected, digest, generation):
    return set_descriptive_selection(
        storage, **SCOPE, selection=selected,
        installed_source_sha256=digest, expected_generation=generation,
    )


def options(binding):
    return dict(
        capture_sources_txn=lambda db: ((fact(),), {}),
        declarations=(DECLARATION,), text_schemas=(SCHEMA,), installed=binding,
        **SCOPE, selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
        target_edition_id=TARGET, now=100, lease_seconds=60,
    )


def test_pin_cas_restart_and_tombstone(tmp_path):
    path = tmp_path / "pin.sqlite"
    db = Storage(path)
    binding = installed()
    selected = selection()
    first = put(db, selected, binding.source_sha256, 0)
    assert first.generation == 1
    assert put(db, selected, binding.source_sha256, 1) == first
    with pytest.raises(ValueError, match="generation_changed"):
        put(db, None, None, 0)
    with pytest.raises(ValueError, match="invalid_descriptive_selection_pin"):
        put(db, replace(selected, scope="foreign"), binding.source_sha256, 1)
    revoked = put(db, None, None, 1)
    assert revoked.generation == 2
    db.close(final=True)
    db = Storage(path)
    try:
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            assert read_descriptive_selection_txn(sql, **SCOPE) == revoked
        with pytest.raises(ValueError, match="descriptive_selection_changed"):
            coordinator.run_descriptive_core_job(db, **options(binding))
        restored = put(db, selected, binding.source_sha256, 2)
        assert restored.generation == 3
    finally:
        db.close(final=True)


@pytest.mark.parametrize("mode", ("revoke", "aba", "digest", "new_pin"))
def test_selection_change_during_worker_refuses_publication(tmp_path, monkeypatch, mode):
    db = Storage(tmp_path / "change.sqlite")
    binding = installed()
    selected = selection()
    if mode != "new_pin":
        put(db, selected, binding.source_sha256, 0)
    original = coordinator.propose_registered_descriptive_materialization

    def changed(**kwargs):
        proposal = original(**kwargs)
        if mode == "new_pin":
            put(db, selected, binding.source_sha256, 0)
        elif mode == "digest":
            put(db, selected, "f"*64, 1)
        else:
            put(db, None, None, 1)
            if mode == "aba":
                put(db, selected, binding.source_sha256, 2)
        return proposal

    monkeypatch.setattr(coordinator, "propose_registered_descriptive_materialization", changed)
    try:
        with pytest.raises(ValueError, match="descriptive_selection_changed"):
            coordinator.run_descriptive_core_job(db, **options(binding))
        assert read_descriptive_materialization(
            db, target_edition_id=TARGET, **SCOPE,
            capture_current_txn=lambda sql: pytest.fail("refused job persisted a row"),
        ) is None
    finally:
        db.close(final=True)


def test_revocation_hides_previous_materialization_even_with_old_context(tmp_path):
    db = Storage(tmp_path / "read.sqlite")
    binding = installed()
    selected = selection()
    try:
        put(db, selected, binding.source_sha256, 0)
        row = coordinator.run_descriptive_core_job(db, **options(binding))
        assert row.value == "13+"
        admitted = admit_descriptive_facts(
            target_edition_id=TARGET, canonical_type="age",
            consolidator_id=selected.plugin_id, consolidator_version=selected.version,
            source_facts=(fact(),), attested_strong_links={},
        )
        read = dict(target_edition_id=TARGET, **SCOPE,
                    capture_current_txn=lambda sql: (selected, admitted, binding.source_sha256))
        assert read_descriptive_materialization(db, **read) == row
        put(db, None, None, 1)
        with pytest.raises(ValueError, match="descriptive_selection_changed"):
            read_descriptive_materialization(db, **read)
    finally:
        db.close(final=True)


@pytest.mark.parametrize("raw", (
    '{"generation":1,"generation":2,"selection":null,"installed_source_sha256":null}',
    '{"generation":true,"selection":null,"installed_source_sha256":null}',
    '{"generation":1,"selection":null,"installed_source_sha256":"invalid"}',
))
def test_corrupt_selection_fails_closed(tmp_path, raw):
    from cifereca.core.notice_descriptive_selection_store import _key
    db = Storage(tmp_path / "corrupt.sqlite")
    try:
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                        (_key(**SCOPE), raw))
        with pytest.raises(ValueError, match="corrupt_descriptive_selection_pin"):
            coordinator.run_descriptive_core_job(db, **options(installed()))
        with pytest.raises(ValueError, match="corrupt_descriptive_selection_pin"):
            put(db, None, None, 0)
    finally:
        db.close(final=True)


def test_reselection_requires_new_publication_generation(tmp_path):
    from cifereca.core import notice_c_job_journal as journal
    from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
    from cifereca.core.notice_descriptive_selection_store import descriptive_selection_input_digest

    db = Storage(tmp_path / "reselection.sqlite")
    binding = installed()
    selected = selection()
    try:
        first = put(db, selected, binding.source_sha256, 0)
        row = coordinator.run_descriptive_core_job(db, **options(binding))
        spec = journal.public_job_spec(
            "recompute_aggregate", canonical_cover_document_ref(TARGET),
            input_digest=descriptive_selection_input_digest(row.execution_key, first),
            implementation_digest=binding.source_sha256,
        )
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            previous = journal._load(sql, journal._key(spec))
        assert previous.state == "current"
        put(db, None, None, 1)
        restored = put(db, selected, binding.source_sha256, 2)
        admitted = admit_descriptive_facts(
            target_edition_id=TARGET, canonical_type="age",
            consolidator_id=selected.plugin_id, consolidator_version=selected.version,
            source_facts=(fact(),), attested_strong_links={},
        )
        read = dict(target_edition_id=TARGET, **SCOPE,
                    capture_current_txn=lambda sql: (selected, admitted, binding.source_sha256))
        with pytest.raises(ValueError, match="stale_descriptive_selection_generation"):
            read_descriptive_materialization(db, **read)
        assert descriptive_selection_input_digest(row.execution_key, first) != (
            descriptive_selection_input_digest(row.execution_key, restored))
        renewed = coordinator.run_descriptive_core_job(
            db, **{**options(binding), "now": 101,
                   "expected_generation": previous.ref.generation,
                   "expected_execution_key": row.execution_key},
        )
        assert renewed == row
        assert read_descriptive_materialization(db, **read) == row
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            current = journal._load(sql, journal._key(spec))
        assert current.state == "current"
        assert current.ref.generation > previous.ref.generation
    finally:
        db.close(final=True)


def test_revocation_before_job_allocation_leaves_no_obsolete_job(tmp_path, monkeypatch):
    from cifereca.core import notice_c_job_journal as journal

    db = Storage(tmp_path / "allocation.sqlite")
    binding = installed()
    selected = selection()
    put(db, selected, binding.source_sha256, 0)
    original = journal.public_job_spec
    specs = []

    def revoke_before_allocation(*args, **kwargs):
        spec = original(*args, **kwargs)
        specs.append(spec)
        put(db, None, None, 1)
        return spec

    monkeypatch.setattr(journal, "public_job_spec", revoke_before_allocation)
    try:
        with pytest.raises(ValueError, match="descriptive_selection_changed"):
            coordinator.run_descriptive_core_job(db, **options(binding))
        assert len(specs) == 1
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            assert journal._load(sql, journal._key(specs[0])) is None
            assert read_descriptive_selection_txn(sql, **SCOPE).generation == 2
    finally:
        db.close(final=True)
