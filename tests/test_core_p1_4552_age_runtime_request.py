"""Q12 explicit runtime producer; no automatic scope or authority inference."""
from contextlib import closing
from dataclasses import replace

from cifereca.storage import Storage
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_work_runtime import NoticeWorkRuntime
from cifereca.core.notice_c_work_bridge import UnavailableNoticeWork
from cifereca.core.notice_descriptive_config_owner import configure_descriptive_selection
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist
from tests.test_core_p1_4981_consolidator_worker import DECLARATION, SCHEMA, installed


def test_runtime_request_requires_explicit_scope_and_supported_pin(tmp_path, monkeypatch):
    with closing(Storage(tmp_path / "age.sqlite")) as db:
        broker = TaskBrokerImpl(object(), max_workers=3, shared_admission=
                                SharedTaskAdmission(max_inflight=4, reserve_user_slots=1))
        runtime = NoticeWorkRuntime(db, broker, clock=lambda: 100)
        binding = installed()
        source = persist(db, project(original()), at=100)
        target = source.observation.key.edition_id
        args = dict(target_edition_id=target, scope="fr:editions", expected_generation=0)
        wake = []
        # Deterministic request/wake receipt, no worker race with assertions.
        monkeypatch.setattr(runtime, "request_tick", lambda: wake.append(True))
        try:
            assert runtime.request_public_age(**args) is None
            def prepare(*_):
                raise UnavailableNoticeWork("fixture")
            assert runtime.install_public_age_execution(
                prepare, implementation_digests=(binding.source_sha256,))
            assert runtime.request_public_age(**args) is None
            persist_exact_public_search_cover(db, source)
            assert runtime.request_public_age(**args) is None
            pin = configure_descriptive_selection(
                db, canonical_type="age", scope=args["scope"],
                selected_plugin_id=DECLARATION.plugin_id, selected_version=DECLARATION.version,
                declarations=(DECLARATION,), text_schemas=(replace(SCHEMA, max_bytes=2048),),
                installed_registry=(binding,), expected_generation=0,
            )
            assert pin.installed_source_sha256 == binding.source_sha256
            assert runtime.request_public_age(**(args | {"scope": "other"})) is None
            assert wake == []
            ref = runtime.request_public_age(**args)
            assert ref is not None and wake == [True]
            assert journal.read_job(db, ref).attempts == 0
            assert runtime.request_public_age(**args) == ref
            assert wake == [True, True]
            changed = installed(SOURCE_CHANGED)
            configure_descriptive_selection(
                db, canonical_type="age", scope=args["scope"],
                selected_plugin_id=DECLARATION.plugin_id, selected_version=DECLARATION.version,
                declarations=(DECLARATION,), text_schemas=(replace(SCHEMA, max_bytes=2048),),
                installed_registry=(changed,), expected_generation=pin.generation,
            )
            assert runtime.request_public_age(**(args | {"expected_generation": ref.generation})) is None
            assert wake == [True, True]
            assert journal.read_job(db, ref).attempts == 0
            runtime.close()
            assert runtime.request_public_age(**args) is None
            assert db.entitlements() == db.acquisitions() == []
        finally:
            runtime.close()
            broker.close()


SOURCE_CHANGED = "def consolidate(facts):\n    return None\n"
