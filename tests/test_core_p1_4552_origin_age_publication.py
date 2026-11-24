from contextlib import closing

import pytest

from cifereca.storage import Storage
from cifereca.core import notice_descriptive_core_coordinator as coordinator
from cifereca.core.notice_c_public_age_consolidation import run_origin_linked_age_descriptive_job
from cifereca.core.notice_descriptive_config_owner import configure_descriptive_selection
from cifereca.core.notice_descriptive_materialization_store import read_descriptive_materialization
from tests.test_core_p1_4552_public_age_inheritance import setup
from tests.test_core_p1_4981_consolidator_worker import DECLARATION, SCHEMA, SOURCE, installed


def test_origin_age_publication_rechecks_relation(tmp_path, monkeypatch):
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

        monkeypatch.setattr(coordinator, "propose_registered_descriptive_materialization", revoke)
        with pytest.raises(ValueError, match="relation_changed|origin_group_required"):
            run_origin_linked_age_descriptive_job(storage, **args)
        assert read_descriptive_materialization(storage, target_edition_id=target,
            **scope, capture_current_txn=lambda sql: pytest.fail("unexpected publication")) is None
