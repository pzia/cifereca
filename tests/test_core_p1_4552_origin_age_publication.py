from contextlib import closing

import pytest

from cifereca.storage import Storage
from cifereca.core import notice_descriptive_core_coordinator as coordinator
from cifereca.core.notice_c_public_age_consolidation import run_origin_linked_age_descriptive_job
from cifereca.core.notice_descriptive_config_owner import configure_descriptive_selection
from cifereca.core.notice_descriptive_materialization_store import read_descriptive_materialization
from cifereca.core.notice_c_cover_index import persist_origin_linked_public_cover_group
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_origin_attested_public_c import _origins, A_URL, B_URL
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist


def setup(storage, tmp_path):
    _origins(storage, tmp_path)
    sources = []
    for provider, record, url in (
        ("seller-a", "edition-a", A_URL), ("seller-b", "edition-b", B_URL),
    ):
        row = original(source_provider=provider, title="L'ouvrage commun", thumbnail_url=url)
        row.record_id = record
        row.metadata["bibliography_evidence"]["youth_age"]["record_id"] = record
        if provider == "seller-a":
            # Inheritance is allowed only when exact evidence is absent.
            row.metadata = {}
        sources.append(persist(storage, project(row), at=200))
    return persist_origin_linked_public_cover_group(storage, sources=tuple(sources)), sources

from tests.test_core_p1_4981_consolidator_worker import DECLARATION, SCHEMA, SOURCE, installed


@pytest.mark.parametrize('revoked', [False, True])
def test_origin_age_publication_rechecks_relation(tmp_path, monkeypatch, revoked):
    with closing(Storage(tmp_path / "publication.sqlite")) as storage:
        group, sources = setup(storage, tmp_path)
        target = sources[0].observation.key.edition_id
        binding = installed(SOURCE.replace('facts["exact"] or facts["inherited"]', 'facts["inherited"]').replace('row["value"]', '"13+"').replace('not bool(facts["exact"])', 'True'))
        scope = dict(canonical_type="age", scope="fr:editions")
        selected = dict(selected_plugin_id=DECLARATION.plugin_id, selected_version=DECLARATION.version)
        configure_descriptive_selection(storage, **scope, **selected,
            declarations=(DECLARATION,), text_schemas=(SCHEMA,),
            installed_registry=(binding,), expected_generation=0)
        args = dict(group_document=group.document, declarations=(DECLARATION,),
                    text_schemas=(SCHEMA,), installed=binding, scope=scope["scope"],
                    target_edition_id=target, now=300, lease_seconds=60, **selected)
        original = coordinator.propose_registered_descriptive_materialization

        def revoke(**kwargs):
            proposal = original(**kwargs)
            assert proposal.materialization.inherited
            with storage._lock, storage._connect() as sql:
                sql.execute("DELETE FROM origin_links")
            return proposal

        if revoked:
            monkeypatch.setattr(coordinator, "propose_registered_descriptive_materialization", revoke)
            with pytest.raises(ValueError, match="relation_changed|origin_group_required"):
                run_origin_linked_age_descriptive_job(storage, **args)
            assert read_descriptive_materialization(storage, target_edition_id=target,
                **scope, capture_current_txn=lambda sql: pytest.fail("unexpected publication")) is None
        else:
            result = run_origin_linked_age_descriptive_job(storage, **args)
            assert result.inherited and result.value == "13+"
            assert result.source_evidence_keys[0][1] == sources[1].observation.key.edition_id

