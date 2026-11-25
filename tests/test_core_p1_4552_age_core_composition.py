"""Q12 composition from independently trusted archive through the real Core pump."""
from hashlib import sha256
import json
from threading import Event
from concurrent.futures import ThreadPoolExecutor
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


def open_core(tmp_path, **options):
    return CiferecaCore.open(
        config_path=tmp_path / "config.json", database_path=tmp_path / "state.sqlite",
        builtin_ui=False, activate_plugins=False, diagnostics_enabled=False,
        notice_work_admission=SharedTaskAdmission(max_inflight=4, reserve_user_slots=1),
        **options,
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
    with open_core(tmp_path, notice_age_registry=registry) as core:
        core.plugins.refresh()
        assert not core.install_notice_age_execution(registry)
        assert core.brokers["tasks"]._executor is None
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
        state_type = type(core.plugins.state)
        original = state_type.set_priority

        def blocking(*args):
            entered.set()
            assert release.wait(10)
            return original(*args)

        monkeypatch.setattr(state_type, "set_priority", blocking)
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker = executor.submit(core.plugins.set_priority, "plugin.age.alpha", None)
            try:
                assert entered.wait(5)
                with pytest.raises(ValueError, match="lifecycle_busy"):
                    with core.plugins.hold_descriptive_lifecycle():
                        pytest.fail("concurrent lifecycle admitted")
            finally:
                release.set()
            worker.result(timeout=10)
        with core.plugins.hold_descriptive_lifecycle():
            core.plugins.refresh()


def test_explicit_startup_registry_requires_admission_and_nonempty_trust(tmp_path):
    data = archive_bytes()
    registry = TrustedConsolidatorRegistry(((data, sha256(data).hexdigest()),))
    config = tmp_path / "config.json"
    database = tmp_path / "state.sqlite"
    for extra in (
        {"notice_age_registry": registry},
        {"notice_age_registry": TrustedConsolidatorRegistry(()),
         "notice_work_admission": SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)},
        {"notice_age_registry": registry, "no_plugins": True,
         "notice_work_admission": SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)},
    ):
        with pytest.raises(ValueError, match="invalid_trusted_consolidator_archives"):
            CiferecaCore.open(config_path=config, database_path=database, **extra)
        assert not database.exists()


def test_desktop_deployment_config_provisions_real_core_without_implicit_selection(tmp_path):
    from cifereca.config import Config
    from cifereca.core.search_orchestration import _ingest_public_search_c

    from pathlib import Path
    from scripts.plugin_packaging import build_plugin_zip

    package = Path(__file__).resolve().parents[1] / "src/plugins/metadata-age-standard"
    archive = build_plugin_zip(package, tmp_path / "dist")
    data = archive.read_bytes()
    # Trusted build owner supplies the independent deployment pin.
    digest = sha256(data).hexdigest()
    config = Config(tmp_path / "config.json")
    config.set("notice_c.public_age_deployment", {
        "archive": str(archive), "sha256": digest, "scope": "public:editions",
    })
    config.save()
    with open_core(tmp_path, notice_age_desktop_config=True) as core:
        assert core._notice_age_hold is not None
        assert callable(core.library._notice_work_request_search_age_txn)
        core.plugins.installer.install(archive)
        core.plugins.refresh()
        # Explicit deployment trust alone must not select a consolidator.
        row = project(original())
        _ingest_public_search_c(core.library, [row], identity_id="A", now=int(time()),
                                require_public_cover_witness=True)
        source = row._core_notice_cover_document_ref
        from cifereca.core.notice_cover_repository import read_notice_cover
        target = read_notice_cover(core.storage, source).observation.key.edition_id
        assert core.read_notice_age(target_edition_id=target, scope="public:editions").status != "current"
        core.configure_notice_consolidator(
            data, expected_sha256=digest, canonical_type="age", scope="public:editions",
            selected_plugin_id="metadata.age.standard", selected_version="1.0.0",
            expected_generation=0,
        )
    with open_core(tmp_path, notice_age_desktop_config=True) as core:
        core.plugins.refresh()
        from tests.test_core_p0_4982_search_atomic_groups import _job_refs
        core.library._notice_work_search_committed = lambda: None
        _ingest_public_search_c(core.library, [project(original())], identity_id="A",
                                now=int(time()), require_public_cover_witness=True)
        refs = _job_refs(core.storage)
        assert len(refs) == 1
        scan = core.notice_work.request_tick()
        result = scan.result(timeout=10)
        handle, = result.handles
        assert handle.result(timeout=15) == "current"
        assert core.read_notice_age(target_edition_id=target, scope="public:editions").status == "current"

    config.set("notice_c.public_age_deployment", {
        "archive": str(archive), "sha256": "0"*64, "scope": "public:editions",
    })
    config.save()
    with pytest.raises(ValueError, match="invalid_desktop_deployment"):
        open_core(tmp_path, notice_age_desktop_config=True)
    # Non-desktop and ordinary no-config startup are unchanged.
    with open_core(tmp_path) as core:
        assert core._notice_age_hold is None
