"""Q12: standard production package, not a promoted test fixture."""
from hashlib import sha256
from pathlib import Path
from time import time

from scripts.plugin_packaging import build_plugin_zip
from cifereca.core.notice_consolidator_archive import qualify_consolidator_archive
from cifereca.core.notice_consolidator_runner import run_registered_text_consolidator
from cifereca.core.notice_consolidator_trusted_registry import TrustedConsolidatorRegistry
from cifereca.core.search_orchestration import _ingest_public_search_c
from cifereca.core.notice_cover_repository import read_notice_cover
from tests.test_core_p1_4552_age_core_composition import open_core
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_4982_search_atomic_groups import _job_refs

PACKAGE = Path(__file__).resolve().parents[1] / "src/plugins/metadata-age-standard"
SCOPE = "public:editions"
PLUGIN = "metadata.age.standard"


def test_standard_package_real_build_install_search_read(tmp_path):
    archive = build_plugin_zip(PACKAGE, tmp_path / "dist")
    data = archive.read_bytes()
    # In this synthetic release receipt the trusted build owner pins bytes.
    # Desktop deployments must use the independent release digest, never
    # recompute expected_sha256 from an arbitrary installed archive.
    digest = sha256(data).hexdigest()
    inventory = qualify_consolidator_archive(data, expected_sha256=digest)
    candidate, = inventory.candidates
    assert candidate.declaration.scope == SCOPE
    registry = TrustedConsolidatorRegistry(((data, digest),))
    with open_core(tmp_path, notice_age_registry=registry, notice_age_search_scope=SCOPE) as core:
        core.plugins.installer.install(archive)
        core.plugins.refresh()
        core.library._notice_work_search_committed = lambda: None
        _ingest_public_search_c(core.library, [project(original())], identity_id="A",
                                now=int(time()), require_public_cover_witness=True)
        assert _job_refs(core.storage) == ()
        core.configure_notice_consolidator(
            data, expected_sha256=digest, canonical_type="age", scope=SCOPE,
            selected_plugin_id=PLUGIN, selected_version="1.0.0", expected_generation=0,
        )
        row = project(original())
        _ingest_public_search_c(core.library, [row], identity_id="A", now=int(time()),
                                require_public_cover_witness=True)
        target = read_notice_cover(core.storage, row._core_notice_cover_document_ref
                                   ).observation.key.edition_id
        assert len(_job_refs(core.storage)) == 1
        scan = core.notice_work.request_tick()
        handle, = scan.result(timeout=10).handles
        assert handle.result(timeout=15) == "current"
        assert core.read_notice_age(target_edition_id=target, scope=SCOPE).status == "current"
        assert core.storage.entitlements() == core.storage.acquisitions() == []


def test_standard_worker_exact_priority_conflict_and_inheritance(tmp_path):
    archive = build_plugin_zip(PACKAGE, tmp_path / "dist")
    data = archive.read_bytes()
    candidate, = qualify_consolidator_archive(
        data, expected_sha256=sha256(data).hexdigest(),
    ).candidates
    def fact(source, edition, value):
        return dict(source_id=source, edition_id=edition, evidence_id="age:0",
                    canonical_type="age", source_revision="revision:1", value=value)
    def run(*facts):
        return run_registered_text_consolidator(
            declarations=(candidate.declaration,), text_schemas=(candidate.schema,),
            installed=candidate.installed, canonical_type="age", scope=SCOPE,
            selected_plugin_id=PLUGIN, selected_version="1.0.0",
            target_edition_id="edition:a", source_facts=facts,
            attested_strong_links={"edition:b": "strong:1"},
        )
    exact = fact("source:a", "edition:a", "9+")
    inherited = fact("source:b", "edition:b", "12+")
    assert run(exact, inherited).value == "9+"
    assert run(inherited).inherited
    assert run(exact, fact("source:c", "edition:a", "12+")) is None
