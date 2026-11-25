"""Origin-linked age shares the exact durable publication fence."""
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from time import time

import pytest

from cifereca.storage import Storage
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_public_age_request import (
    request_public_age_consolidation, reattest_public_age_job_txn,
)
from cifereca.core.notice_descriptive_config_owner import configure_descriptive_selection
from cifereca.core.notice_consolidator_trusted_registry import TrustedConsolidatorRegistry
from tests.test_core_p1_4552_origin_age_publication import setup
from tests.test_core_p1_4981_consolidator_worker import DECLARATION, SCHEMA, installed
from tests.test_core_p1_4552_age_core_composition import archive_bytes, open_core


def test_origin_locator_roundtrips_and_revoked_relation_refuses_reattest(tmp_path):
    path = tmp_path / "age.sqlite"
    with closing(Storage(path)) as storage:
        group, sources = setup(storage, tmp_path)
        target = sources[0].observation.key.edition_id
        args = dict(target_edition_id=target, scope="fr:editions", now=300,
                    expected_generation=0, group_document=group.document)
        configure_descriptive_selection(
            storage, canonical_type="age", scope=args["scope"],
            selected_plugin_id=DECLARATION.plugin_id, selected_version=DECLARATION.version,
            declarations=(DECLARATION,), text_schemas=(SCHEMA,),
            installed_registry=(installed(),), expected_generation=0,
        )
        ref = request_public_age_consolidation(storage, **args)
        spec = journal.read_job(storage, ref).spec
        assert spec.public_age_locator == (target, args["scope"], group.document.source_key)
        assert request_public_age_consolidation(storage, **args) == ref
        with pytest.raises(ValueError, match="invalid_descriptive"):
            journal._check_spec(replace(spec, public_age_locator=(target, args["scope"], "bad")))
    with closing(Storage(path)) as storage:
        assert journal.read_job(storage, ref).spec == spec
        with storage._lock, storage._connect() as sql:
            sql.execute("BEGIN")
            _, admitted = reattest_public_age_job_txn(sql, spec)
            assert admitted.inherited and not admitted.exact
            sql.execute("DELETE FROM origin_links")
            with pytest.raises(ValueError, match="relation_changed|origin_group_required"):
                reattest_public_age_job_txn(sql, spec)
        assert storage.entitlements() == storage.acquisitions() == []


def test_origin_group_runs_through_core_and_read_refuses_revocation(tmp_path):
    data = archive_bytes()
    digest = sha256(data).hexdigest()
    archive = tmp_path / "age.zip"
    archive.write_bytes(data)
    registry = TrustedConsolidatorRegistry(((data, digest),))
    with open_core(tmp_path) as core:
        core.plugins.installer.install(archive)
        core.plugins.refresh()
        core.configure_notice_consolidator(
            data, expected_sha256=digest, canonical_type="age", scope="fr:editions",
            selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
            expected_generation=0,
        )
        group, sources = setup(core.storage, tmp_path)
        args = dict(target_edition_id=sources[0].observation.key.edition_id,
                    scope="fr:editions", group_document=group.document)
        ref = request_public_age_consolidation(core.storage, **args, now=int(time()),
                                               expected_generation=0)
        assert core.install_notice_age_execution(registry)
        handle, = core.notice_work.request_tick().result(timeout=10).handles
        assert handle.result(timeout=15) == "current"
        assert journal.read_job(core.storage, ref).state == "current"
        result = core.read_notice_age(**args)
        assert result.status == "current" and result.materialization.inherited
        assert result.materialization.source_evidence_keys[0][1] == sources[1].observation.key.edition_id
        with core.storage._lock, core.storage._connect() as sql:
            sql.execute("DELETE FROM origin_links")
        assert core.read_notice_age(**args).status == "stale"
        assert core.read_notice_age(**args).materialization is None
        assert core.storage.entitlements() == core.storage.acquisitions() == []
