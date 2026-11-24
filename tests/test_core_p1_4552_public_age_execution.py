from contextlib import closing, contextmanager
from hashlib import sha256
import json

import pytest

from cifereca.storage import Storage
from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_public_age_request import request_public_age_consolidation, reattest_public_age_job_txn
from cifereca.core.notice_c_public_age_execution import prepare_exact_public_age
from cifereca.core.notice_descriptive_active_config import reconcile_active_descriptive_selection
from cifereca.core.notice_consolidator_active_inventory import hold_active_consolidator_inventory
from cifereca.core.notice_descriptive_materialization_store import read_descriptive_materialization
from cifereca.core.notice_c_work_bridge import AggregateWorkBridge
from cifereca.task_runtime import TaskBrokerImpl
from cifereca.task_admission import SharedTaskAdmission
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist
from tests.test_core_p1_4552_consolidator_archive import payload
from tests.test_core_p1_4552_consolidator_manifest import row
from tests.test_core_p1_4981_consolidator_worker import SOURCE


@pytest.mark.parametrize("revoke", (False, True))
def test_durable_public_age_executes_real_worker_and_rechecks_archive(tmp_path, revoke):
    source_bytes = SOURCE.encode()
    data = payload(source=source_bytes, manifest=json.dumps(dict(
        manifest=1, package=dict(id="package.age", version="1.0.0"),
        plugins=[dict(id="plugin.age.alpha", type="meta.bundle", version="1.0.0",
                      capabilities=["metadata.consolidator"])],
        notice_text_consolidators=[row(source_sha256=sha256(source_bytes).hexdigest(),
                                       max_bytes=2048, allowed_values=[])],
    )))
    archive = tmp_path / "age.zip"
    archive.write_bytes(data)
    manager = PluginManager(package_store=PackageStore(tmp_path / "packages"),
                            state_store=PluginStateStore(tmp_path / "state.json"))
    scope = "fr:editions"
    digest = sha256(data).hexdigest()
    try:
        manager.installer.install(archive)
        manager.refresh()
        with closing(Storage(tmp_path / "age.sqlite")) as db:
            source = persist(db, project(original()), at=100)
            persist_exact_public_search_cover(db, source)
            target = source.observation.key.edition_id
            pin = reconcile_active_descriptive_selection(
                db, manager, data, expected_sha256=digest, canonical_type="age", scope=scope,
                selected_plugin_id="plugin.age.alpha", selected_version="1.0.0", expected_generation=0,
            )
            ref = request_public_age_consolidation(db, target_edition_id=target,
                                                   scope=scope, now=100, expected_generation=0)
            spec = journal.read_job(db, ref).spec
            seen = []

            @contextmanager
            def hold(selected):
                assert selected == pin
                seen.append(selected)
                if revoke and len(seen) == 2:
                    manager.state.set_enabled("plugin.age.alpha", False)
                with hold_active_consolidator_inventory(manager, data, expected_sha256=digest) as active:
                    yield active[0] if active else None

            broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=
                                    SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))
            try:
                bridge = AggregateWorkBridge(
                    db, broker, lambda *_: pytest.fail("wrong worker"), clock=lambda: 100,
                    prepare_descriptive=lambda spec, control: prepare_exact_public_age(
                        db, spec, control, hold_selected=hold),
                )
                handle = bridge.offer(ref, circuit="local").handle
                assert handle.result(timeout=15) == ("error" if revoke else "current")
                assert len(seen) == 2
                assert journal.read_job(db, ref).state == ("error" if revoke else "current")
                with hold_active_consolidator_inventory(manager, data, expected_sha256=digest) as active:
                    schema = active[0].schema if active else None

                def capture(sql):
                    latest, admitted = reattest_public_age_job_txn(sql, spec)
                    return latest.selection, admitted, latest.installed_source_sha256

                result = read_descriptive_materialization(
                    db, target_edition_id=target, canonical_type="age", scope=scope,
                    capture_current_txn=capture, schema=schema,
                )
                if revoke:
                    assert result is None
                else:
                    assert "age_min" in result.value and not result.inherited
                assert db.entitlements() == db.acquisitions() == []
            finally:
                broker.close()
    finally:
        manager.close()
