"""Q12 composition from independently trusted archive through the real Core pump."""
from hashlib import sha256
import json
from threading import Event, Thread
from time import time

import pytest

from cifereca.core.runtime import CiferecaCore
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_consolidator_trusted_registry import TrustedConsolidatorRegistry
from cifereca.core.notice_c_public_age_request import (
    request_public_age_consolidation, reattest_public_age_job_txn,
)
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_descriptive_materialization_store import read_descriptive_materialization
from cifereca.task_admission import SharedTaskAdmission
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist
from tests.test_core_p1_4552_consolidator_archive import payload
from tests.test_core_p1_4552_consolidator_manifest import row
from tests.test_core_p1_4981_consolidator_worker import SOURCE


def archive_bytes():
    script = SOURCE.encode()
    return payload(source=script, manifest=json.dumps(dict(
        manifest=1, package=dict(id="package.age", version="1.0.0"),
        plugins=[dict(id="plugin.age.alpha", type="meta.bundle", version="1.0.0",
                      capabilities=["metadata.consolidator"])],
        notice_text_consolidators=[row(source_sha256=sha256(script).hexdigest(),
                                       max_bytes=2048, allowed_values=[])],
    )))


def open_core(tmp_path):
    return CiferecaCore.open(
        config_path=tmp_path / "config.json", database_path=tmp_path / "state.sqlite",
        builtin_ui=False, activate_plugins=False, diagnostics_enabled=False,
        notice_work_admission=SharedTaskAdmission(max_inflight=4, reserve_user_slots=1),
    )


def test_core_explicit_registry_executes_durable_job_after_restart(tmp_path):
    data = archive_bytes()
    digest = sha256(data).hexdigest()
    archive = tmp_path / "age.zip"
    archive.write_bytes(data)
    registry = TrustedConsolidatorRegistry(((data, digest),))
    with open_core(tmp_path) as core:
        core.plugins.installer.install(archive)
        core.plugins.refresh()
        pin = core.configure_notice_consolidator(
            data, expected_sha256=digest, canonical_type="age", scope="fr:editions",
            selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
            expected_generation=0,
        )
        source = persist(core.storage, project(original()), at=int(time()))
        persist_exact_public_search_cover(core.storage, source)
        target = source.observation.key.edition_id
        ref = request_public_age_consolidation(
            core.storage, target_edition_id=target, scope="fr:editions",
            now=int(time()), expected_generation=0,
        )
        assert core.brokers["tasks"]._executor is None
    with open_core(tmp_path) as core:
        core.plugins.refresh()
        assert not core.install_notice_age_execution(TrustedConsolidatorRegistry(()))
        assert core.install_notice_age_execution(registry)
        assert not core.install_notice_age_execution(registry)
        scan = core.notice_work.request_tick()
        result = scan.result(timeout=10)
        handle, = result.handles
        assert handle.result(timeout=15) == "current"
        job = journal.read_job(core.storage, ref)
        assert job.state == "current"
        with registry.hold_selected(core.plugins, pin) as candidate:
            schema = candidate.schema

        def capture(sql):
            current, admitted = reattest_public_age_job_txn(sql, job.spec)
            return current.selection, admitted, current.installed_source_sha256

        materialized = read_descriptive_materialization(
            core.storage, target_edition_id=target, canonical_type="age",
            scope="fr:editions", capture_current_txn=capture, schema=schema,
        )
        assert "age_min" in materialized.value
        assert not materialized.inherited
        assert core.storage.entitlements() == core.storage.acquisitions() == []


def test_lifecycle_guard_refuses_concurrent_mutation_without_waiting(tmp_path, monkeypatch):
    with open_core(tmp_path) as core:
        entered, release = Event(), Event()
        original = core.plugins.state.set_priority

        def blocking(*args):
            entered.set()
            assert release.wait(10)
            return original(*args)

        monkeypatch.setattr(core.plugins.state, "set_priority", blocking)
        worker = Thread(target=core.plugins.set_priority, args=("plugin.age.alpha", None))
        worker.start()
        try:
            assert entered.wait(5)
            with pytest.raises(ValueError, match="lifecycle_busy"):
                with core.plugins.hold_descriptive_lifecycle():
                    pytest.fail("concurrent lifecycle admitted")
        finally:
            release.set()
            worker.join(10)
        assert not worker.is_alive()
        with core.plugins.hold_descriptive_lifecycle():
            core.plugins.refresh()
