from dataclasses import replace

import pytest

from cifereca.storage import Storage
from cifereca.core.notice_descriptive_config_owner import configure_descriptive_selection
from cifereca.core.notice_descriptive_selection_store import read_descriptive_selection_txn
from tests.test_core_p1_4981_consolidator_worker import DECLARATION, SCHEMA, installed

SCOPE = dict(canonical_type="age", scope="fr:editions")


def configure(storage, generation, **changes):
    options = dict(
        declarations=(DECLARATION,), text_schemas=(SCHEMA,),
        installed_registry=(installed(),), **SCOPE,
        selected_plugin_id=DECLARATION.plugin_id,
        selected_version=DECLARATION.version, expected_generation=generation,
    )
    return configure_descriptive_selection(storage, **{**options, **changes})


def test_configuration_never_auto_selects_and_revokes_removed_version(tmp_path):
    storage = Storage(tmp_path / "config.sqlite")
    try:
        empty = configure(storage, 0, selected_plugin_id=None, selected_version=None)
        assert empty.selection is None and empty.generation == 1
        selected = configure(storage, 1)
        assert selected.selection.plugin_id == DECLARATION.plugin_id
        assert selected.installed_source_sha256 == installed().source_sha256
        assert configure(storage, 2) == selected
        missing = configure(storage, 2, installed_registry=())
        assert missing.selection is None and missing.generation == 3
        assert configure(storage, 3, selected_version="9.9.9") == missing
    finally:
        storage.close(final=True)


@pytest.mark.parametrize("changes,reason", [
    ({"installed_registry": (installed(), installed())}, "ambiguous_installed"),
    ({"installed_registry": (installed(source_sha256="a"*64),)}, "installed_consolidator_mismatch"),
    ({"text_schemas": (SCHEMA, SCHEMA)}, "ambiguous_canonical_text_schema"),
    ({"installed_registry": (replace(installed(), scope="other"),)}, "installed_consolidator_mismatch"),
])
def test_invalid_registry_preserves_previous_pin(tmp_path, changes, reason):
    storage = Storage(tmp_path / "refused.sqlite")
    try:
        selected = configure(storage, 0)
        with pytest.raises(ValueError, match=reason):
            configure(storage, 1, **changes)
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            assert read_descriptive_selection_txn(db, **SCOPE) == selected
    finally:
        storage.close(final=True)


def test_source_change_without_version_bump_advances_generation(tmp_path):
    storage = Storage(tmp_path / "source.sqlite")
    try:
        first = configure(storage, 0)
        new = installed(installed().source + "\n# revised\n")
        second = configure(storage, 1, installed_registry=(new,))
        assert second.selection == first.selection
        assert second.generation == 2
        assert second.installed_source_sha256 == new.source_sha256
        with pytest.raises(ValueError, match="generation_changed"):
            configure(storage, 1)
    finally:
        storage.close(final=True)


def test_schema_change_without_version_bump_fences_existing_result(tmp_path):
    from cifereca.core.notice_descriptive_core_coordinator import run_descriptive_core_job
    from cifereca.core.notice_descriptive_materialization_store import read_descriptive_materialization
    from cifereca.core.notice_consolidation_inputs import admit_descriptive_facts
    from tests.test_core_p1_4552_descriptive_selection_store import options
    from tests.test_core_p1_4981_consolidator_worker import TARGET, fact

    storage = Storage(tmp_path / "schema.sqlite")
    binding = installed()
    try:
        first = configure(storage, 0)
        assert first.schema_sha256 is not None
        published = run_descriptive_core_job(storage, **options(binding))
        selected = first.selection
        admitted = admit_descriptive_facts(
            target_edition_id=TARGET, canonical_type="age",
            consolidator_id=selected.plugin_id, consolidator_version=selected.version,
            source_facts=(fact(),), attested_strong_links={},
        )
        read = dict(target_edition_id=TARGET, **SCOPE,
                    capture_current_txn=lambda sql: (selected, admitted, binding.source_sha256))
        assert read_descriptive_materialization(storage, **read, schema=SCHEMA) == published
        with pytest.raises(ValueError, match="schema_changed"):
            read_descriptive_materialization(storage, **read)
        changed = replace(SCHEMA, allowed_values=("16+",))
        second = configure(storage, 1, text_schemas=(changed,))
        assert second.generation == 2 and second.selection == first.selection
        assert second.schema_sha256 != first.schema_sha256
        with pytest.raises(ValueError, match="schema_changed"):
            run_descriptive_core_job(storage, **options(binding))
        with pytest.raises(ValueError, match="schema_changed"):
            read_descriptive_materialization(storage, **read, schema=SCHEMA)
        with pytest.raises(ValueError, match="stale_descriptive_selection_generation"):
            read_descriptive_materialization(storage, **read, schema=changed)
    finally:
        storage.close(final=True)
