"""#4552: actual public source -> SQLite -> isolated worker -> journal -> restart."""
from dataclasses import replace
from cifereca.storage import Storage
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import persist_notice_cover
from cifereca.core.notice_c_public_age_consolidation import run_public_age_descriptive_job
from cifereca.core.notice_c_public_age_sql_snapshot import capture_public_age_sources_txn
from cifereca.core.notice_descriptive_materialization_store import read_descriptive_materialization
from cifereca.core.notice_consolidation_inputs import admit_descriptive_facts
from cifereca.core.notice_consolidator_selection import select_consolidator
from tests.test_core_p1_4552_public_age_descriptive_facts import observation
from tests.test_core_p1_4981_consolidator_worker import (
    DECLARATION, SCHEMA, installed,
)


def test_real_public_age_consolidation_survives_restart(tmp_path):
    path = tmp_path / "public-age.sqlite"
    storage = Storage(path)
    source = observation()
    document = notice_cover_document_ref(source.key)
    persist_notice_cover(
        storage, source, field_path=("search_result", "thumbnail_url"),
        cover_inventory_complete=False,
    )
    # The public observation is structured JSON; the selected plugin schema
    # must explicitly allow its serialized representation.
    schema = replace(SCHEMA, max_bytes=4096)
    binding = installed()
    options = dict(
        source_documents=(document,), declarations=(DECLARATION,),
        text_schemas=(schema,), installed=binding, scope="fr:editions",
        selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
        target_edition_id=source.key.edition_id, now=100, lease_seconds=60,
    )
    published = run_public_age_descriptive_job(storage, **options)
    assert published is not None
    assert "age_min" in published.value
    reopened = Storage(path)
    selection = select_consolidator(
        (DECLARATION,), canonical_type="age", scope="fr:editions",
        selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
    )

    def capture(db):
        facts, links = capture_public_age_sources_txn(
            db, documents=(document,), target_edition_id=source.key.edition_id,
        )
        return selection, admit_descriptive_facts(
            target_edition_id=source.key.edition_id,
            canonical_type="age", consolidator_id=selection.plugin_id,
            consolidator_version=selection.version, source_facts=facts,
            attested_strong_links=links,
        ), binding.source_sha256

    assert read_descriptive_materialization(
        reopened, target_edition_id=source.key.edition_id,
        canonical_type="age", scope="fr:editions",
        capture_current_txn=capture,
    ) == published
    assert run_public_age_descriptive_job(reopened, **options) is None
