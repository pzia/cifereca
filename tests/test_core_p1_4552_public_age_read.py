"""Q12 consumer read reattests real archive, C/source and selection."""
from contextlib import closing, contextmanager
from hashlib import sha256
import json

from cifereca.storage import Storage
from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_public_age_read import read_public_age_result, PublicAgeRead
from cifereca.core.notice_c_public_age_request import request_public_age_consolidation
from cifereca.core.notice_c_public_age_execution import prepare_exact_public_age
from cifereca.core.notice_descriptive_active_config import reconcile_active_descriptive_selection
from cifereca.core.notice_consolidator_trusted_registry import TrustedConsolidatorRegistry
from cifereca.core.notice_descriptive_selection_store import set_descriptive_selection
from cifereca.core.notice_c_job_retention import recycle_terminal_jobs
from cifereca.core.notice_c_work_bridge import AggregateWorkBridge
from cifereca.task_runtime import TaskBrokerImpl
from cifereca.task_admission import SharedTaskAdmission
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist
from tests.test_core_p1_4552_consolidator_archive import payload
from tests.test_core_p1_4552_consolidator_manifest import row
from tests.test_core_p1_4981_consolidator_worker import SOURCE


def test_public_age_read_current_then_unavailable_stale_and_unconfigured(tmp_path):
    script = SOURCE.encode()
    data = payload(source=script, manifest=json.dumps(dict(
        manifest=1, package=dict(id="package.age", version="1.0.0"),
        plugins=[dict(id="plugin.age.alpha", type="meta.bundle", version="1.0.0",
                      capabilities=["metadata.consolidator"])],
        notice_text_consolidators=[row(source_sha256=sha256(script).hexdigest(),
                                       max_bytes=2048, allowed_values=[])],
    )))
    archive = tmp_path / "age.zip"
    archive.write_bytes(data)
    digest = sha256(data).hexdigest()
    registry = TrustedConsolidatorRegistry(((data, digest),))
    manager = PluginManager(package_store=PackageStore(tmp_path / "packages"),
                            state_store=PluginStateStore(tmp_path / "state.json"))
    try:
        manager.installer.install(archive)
        manager.refresh()
        with closing(Storage(tmp_path / "age.sqlite")) as db:
            source = persist(db, project(original()), at=100)
            persist_exact_public_search_cover(db, source)
            target, scope = source.observation.key.edition_id, "fr:editions"
            def hold(pin):
                return registry.hold_selected(manager, pin)

            def read():
                return read_public_age_result(db, target_edition_id=target, scope=scope,
                                              hold_selected=hold)

            assert read() == PublicAgeRead("unconfigured")
            pin = reconcile_active_descriptive_selection(
                db, manager, data, expected_sha256=digest, canonical_type="age", scope=scope,
                selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
                expected_generation=0,
            )
            assert read() == PublicAgeRead("unknown")
            ref = request_public_age_consolidation(db, target_edition_id=target, scope=scope,
                                                   now=100, expected_generation=0)
            broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=
                                    SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))
            try:
                bridge = AggregateWorkBridge(
                    db, broker, lambda *_: None, clock=lambda: 100,
                    prepare_descriptive=lambda spec, control: prepare_exact_public_age(
                        db, spec, control, hold_selected=hold),
                )
                assert bridge.offer(ref, circuit="local").handle.result(timeout=15) == "current"
                assert journal.read_job(db, ref).state == "current"
                current = read()
                assert current.status == "current"
                assert "age_min" in current.materialization.value
                assert not current.materialization.inherited
                # The read is lineage/pin bound, not dependent on retention
                # of a terminal job receipt.
                recycle_terminal_jobs(db, now=1000000)
                assert read() == current
                manager.state.set_enabled("plugin.age.alpha", False)
                assert read() == PublicAgeRead("unavailable")
                manager.state.set_enabled("plugin.age.alpha", True)
                manager.refresh()
                assert read() == current
                changed = original()
                changed.metadata["bibliography_evidence"]["youth_age"]["ranges"][0]["age_min"] = 14
                latest = persist(db, project(changed), at=101)
                persist_exact_public_search_cover(db, latest)
                assert read() == PublicAgeRead("stale")
                set_descriptive_selection(db, canonical_type="age", scope=scope, selection=None,
                                          installed_source_sha256=None,
                                          expected_generation=pin.generation)
                assert read() == PublicAgeRead("unconfigured")
                assert db.entitlements() == db.acquisitions() == []
            finally:
                broker.close()
    finally:
        manager.close()
