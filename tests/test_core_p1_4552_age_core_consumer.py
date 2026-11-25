"""Qualified Core consumer uses the very same authority as execution."""
from hashlib import sha256
from threading import Event
from time import monotonic, time

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_consolidator_trusted_registry import TrustedConsolidatorRegistry
from tests.test_core_p1_4552_age_core_composition import archive_bytes, open_core
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist


def test_core_consumer_request_and_read_share_trusted_authority(tmp_path):
    data = archive_bytes()
    digest = sha256(data).hexdigest()
    archive = tmp_path / "age.zip"
    archive.write_bytes(data)
    registry = TrustedConsolidatorRegistry(((data, digest),))
    args = dict(scope="fr:editions")
    with open_core(tmp_path) as core:
        core.plugins.installer.install(archive)
        core.plugins.refresh()
        source = persist(core.storage, project(original()), at=int(time()))
        persist_exact_public_search_cover(core.storage, source)
        args["target_edition_id"] = source.observation.key.edition_id
        assert core.read_notice_age(**args).status == "unavailable"
        assert core.install_notice_age_execution(registry)
        assert core.read_notice_age(**args).status == "unconfigured"
        core.configure_notice_consolidator(
            data, expected_sha256=digest, canonical_type="age", scope=args["scope"],
            selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
            expected_generation=0,
        )
        assert core.read_notice_age(**args).status == "unknown"
        ref = core.notice_work.request_public_age(**args, expected_generation=0)
        deadline = monotonic() + 15
        while journal.read_job(core.storage, ref).state != "current" and monotonic() < deadline:
            assert journal.read_job(core.storage, ref).state != "error"
            Event().wait(.01)
        assert journal.read_job(core.storage, ref).state == "current"
        # SQL commit precedes release of the nonblocking lifecycle guard.
        # A concurrent read may legitimately fail closed during that interval.
        result = core.read_notice_age(**args)
        while result.status == "unavailable" and monotonic() < deadline:
            Event().wait(.01)
            result = core.read_notice_age(**args)
        assert result.status == "current"
        assert "age_min" in result.materialization.value
        assert not result.materialization.inherited
        core.plugins.set_enabled("plugin.age.alpha", False)
        revoked = core.read_notice_age(**args)
        assert revoked.status == "unconfigured" and revoked.materialization is None
        assert core.storage.entitlements() == core.storage.acquisitions() == []
    assert core.read_notice_age(**args).status == "unavailable"


def test_search_commit_triggers_selected_age_without_manual_request(tmp_path, monkeypatch):
    from cifereca.core.search_orchestration import _ingest_public_search_c
    from tests.test_core_p0_4982_search_atomic_groups import _job_refs

    data = archive_bytes()
    digest = sha256(data).hexdigest()
    archive = tmp_path / "age.zip"
    archive.write_bytes(data)
    registry = TrustedConsolidatorRegistry(((data, digest),))
    with open_core(tmp_path, notice_age_registry=registry,
                   notice_age_search_scope="fr:editions") as core:
        core.plugins.installer.install(archive)
        core.plugins.refresh()
        row = project(original())
        core.configure_notice_consolidator(
            data, expected_sha256=digest, canonical_type="age", scope="fr:editions",
            selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
            expected_generation=0,
        )
        wakes = []
        with monkeypatch.context() as patch:
            patch.setattr(core.library, "_notice_work_search_committed",
                          lambda: wakes.extend(_job_refs(core.storage)))
            pairs = _ingest_public_search_c(
                core.library, [row], identity_id="alice", now=int(time()),
                require_public_cover_witness=True,
            )
        (_, source, canonical), = pairs
        assert canonical is not None
        ref, = wakes
        assert _job_refs(core.storage) == (ref,)
        job = journal.read_job(core.storage, ref)
        assert job.state == "pending" and job.attempts == 0
        args = dict(target_edition_id=source.observation.key.edition_id,
                    scope="fr:editions")
        assert core.read_notice_age(**args).status == "unknown"
        handle, = core.notice_work.request_tick().result(timeout=10).handles
        assert handle.result(timeout=15) == "current"
        assert journal.read_job(core.storage, ref).state == "current"
        assert core.read_notice_age(**args).status == "current"
        assert core.storage.entitlements() == core.storage.acquisitions() == []
    with open_core(tmp_path, notice_age_registry=registry,
                   notice_age_search_scope="fr:editions") as core:
        core.plugins.refresh()
        assert core.read_notice_age(**args).status == "current"
        core.plugins.set_enabled("plugin.age.alpha", False)
        assert core.read_notice_age(**args).status == "unconfigured"


def test_search_group_triggers_directed_inherited_age(tmp_path, monkeypatch):
    """Q9: actual origin-linked Search commit, worker and read; no manual job."""
    from cifereca.core.search_orchestration import _ingest_public_search_c
    from tests.test_core_p0_4982_search_atomic_groups import _job_refs
    from tests.test_core_p0_6366_origin_attested_public_c import _origins, A_URL, B_URL

    data = archive_bytes()
    digest = sha256(data).hexdigest()
    archive = tmp_path / "age.zip"
    archive.write_bytes(data)
    registry = TrustedConsolidatorRegistry(((data, digest),))
    with open_core(tmp_path, notice_age_registry=registry,
                   notice_age_search_scope="fr:editions") as core:
        _origins(core.storage, tmp_path)
        core.plugins.installer.install(archive)
        core.plugins.refresh()
        core.configure_notice_consolidator(
            data, expected_sha256=digest, canonical_type="age", scope="fr:editions",
            selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
            expected_generation=0,
        )
        a = original(source_provider="seller-a", title="L'ouvrage commun", thumbnail_url=A_URL)
        b = original(source_provider="seller-b", title="L'ouvrage commun", thumbnail_url=B_URL)
        a.record_id = "edition-a"
        b.record_id = "edition-b"
        a.metadata["bibliography_evidence"]["youth_age"]["record_id"] = a.record_id
        b.metadata = {}
        before = (core.storage.entitlements(), core.storage.acquisitions())
        with monkeypatch.context() as patch:
            patch.setattr(core.library, "_notice_work_search_committed", lambda: None)
            rows = [project(a), project(b)]
            pairs = _ingest_public_search_c(
                core.library, rows, identity_id="A", now=int(time()),
                require_public_cover_witness=True,
            )
        assert len(pairs) == 2
        group = rows[0]._core_notice_canonical_cover_document_ref
        assert rows[1]._core_notice_canonical_cover_document_ref == group
        args = dict(target_edition_id=pairs[1][1].observation.key.edition_id,
                    scope="fr:editions", group_document=group)
        refs = _job_refs(core.storage)
        assert len(refs) == 2
        deadline = monotonic() + 20
        while monotonic() < deadline:
            if all(journal.read_job(core.storage, ref).state == "current" for ref in refs):
                break
            scan = core.notice_work.request_tick()
            if scan is not None:
                for handle in scan.result(timeout=10).handles:
                    handle.result(timeout=15)
            Event().wait(.01)
        assert all(journal.read_job(core.storage, ref).state == "current" for ref in refs)
        result = core.read_notice_age(**args)
        assert result.status == "current"
        assert result.materialization.inherited
        assert "age_min" in result.materialization.value
        with core.storage._lock, core.storage._connect() as db:
            db.execute("DELETE FROM origin_links")
        assert core.read_notice_age(**args).status != "current"
        assert (core.storage.entitlements(), core.storage.acquisitions()) == before
