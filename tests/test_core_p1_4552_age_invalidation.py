"""Successful Q12 publication invalidates documentary consumers, not covers."""
from hashlib import sha256
from time import time

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_consolidator_trusted_registry import TrustedConsolidatorRegistry
from cifereca.core.notice_c_public_age_request import request_public_age_consolidation
from tests.test_core_p1_4552_age_core_composition import archive_bytes, open_core
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist


def test_descriptive_commit_only_notifies_documentary_revision(tmp_path):
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
        source = persist(core.storage, project(original()), at=int(time()))
        persist_exact_public_search_cover(core.storage, source)
        ref = request_public_age_consolidation(
            core.storage, target_edition_id=source.observation.key.edition_id,
            scope="fr:editions", now=int(time()), expected_generation=0,
        )
        runtime = core.notice_work
        assert core.install_notice_age_execution(registry)
        covers = []
        runtime.bind_committed_observer(lambda *args: covers.append(args))
        before = runtime.documentary_revision()
        handle, = runtime.request_tick().result(timeout=10).handles
        assert handle.result(timeout=15) == "current"
        assert journal.read_job(core.storage, ref).state == "current"
        assert runtime.documentary_revision() == before + 1
        assert covers == []
        assert core.storage.entitlements() == core.storage.acquisitions() == []
