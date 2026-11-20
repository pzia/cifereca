"""#773: generated EPUB/PDF -> real optimizer -> durable Core publication."""
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl
from cifereca.core.derived_variant_journal import summarize_derived_journal
from cifereca.plugin_api import ArtifactFormat, ArtifactRef, TransformRequest
from cifereca.storage import Storage
from cifereca_epub_optimizer.builtin_profiles import builtin_profile
from cifereca_epub_optimizer_transformer import EpubOptimizerTransformer
from cifereca_pdf_optimizer_transformer import PdfOptimizerTransformer
from tests.test_p0_773_generated_documents import documents


@pytest.mark.parametrize("filename,fmt,media,cls,plugin,capability", [
    ("optimizer-valid.epub", "epub", "application/epub+zip",
     EpubOptimizerTransformer, "document.transformer.epub-optimizer", "epub.image-optimize-eink"),
    ("publication-scan.pdf", "pdf", "application/pdf",
     PdfOptimizerTransformer, "document.transformer.pdf-eink-optimizer", "pdf.image-optimize-eink"),
])
def test_real_optimizer_publication_and_restart_replay(
    documents, tmp_path, monkeypatch, filename, fmt, media, cls, plugin, capability,
):
    dataset, manifest = documents
    fixture = dataset / filename
    original = fixture.read_bytes()
    digest = sha256(original).hexdigest()
    assert digest == manifest["files"][filename]
    root = tmp_path / "library"
    parent_a = root / "A" / filename
    parent_b = root / "B" / filename
    for parent in (parent_a, parent_b):
        parent.parent.mkdir(parents=True)
        parent.write_bytes(original)
    database = tmp_path / "state.sqlite"
    db = Storage(database)
    try:
        aid = "sha256:" + digest
        db.artifact_put({"artifact_id": aid, "sha256": digest, "size": len(original),
                         "media_format": fmt, "mime_type": media})
        pids = {}
        for owner, parent in (("A", parent_a), ("B", parent_b)):
            db.identity_put({"identity_id": owner, "directory_name": owner, "label": owner})
            pids[owner] = db.placement_put({"artifact_id": aid, "identity_id": owner,
                                           "path": str(parent), "state": "present"})
        source = ArtifactRef(aid, "synthetic-book", ArtifactFormat(media, fmt, "none"),
                             len(original), digest, filename=filename,
                             identity_id="A", placement_id=pids["A"])
        profile = "device.profile.generic-eink"
        transformer = cls()
        transformer.context = NS(config={}, report_diagnostic=lambda *_a, **_kw: None)
        # Only registry/profile discovery is synthetic; plan and transform are
        # the production implementations, and the actual Core owns all writes.
        monkeypatch.setattr(transformer, "_profile", lambda _request: (
            profile, builtin_profile("generic-eink")))
        resolved = NS(plugin_id=plugin, service=transformer, registry_generation=1,
                      capabilities=("artifact.plan", "artifact.transform"))
        manager = NS(services=lambda category: (resolved,) if category == "document.transformer" else (),
                     statuses=lambda: tuple(NS(descriptor=NS(id=key, version="1"))
                                            for key in (plugin, profile)))
        service = NS(storage=db, config={"library.directory": str(root)},
                     current_identity=NS(identity_id="A"), plugin_manager=manager)
        from cifereca import paths
        monkeypatch.setattr(paths, "acquisition_lock_file", lambda: tmp_path / "acquisition.lock")

        def broker_for_service():
            broker = ArtifactExecutionBrokerImpl(service)
            assert broker._durable_variant_publication is False
            broker._durable_variant_publication = True  # isolated synthetic test only
            return broker

        request = TransformRequest(source, capability, profile_plugin_id=profile,
                                   identity_id="A", placement_id=pids["A"])
        broker = broker_for_service()
        result = broker.admit_transform(request, transformer_plugin_id=plugin)()
        assert result.artifact_id != aid
        placement, = [p for p in db.placements() if p["artifact_id"] == result.artifact_id]
        assert placement["identity_id"] == "A"
        output = Path(placement["path"])
        assert output.parent == parent_a.parent
        assert sha256(output.read_bytes()).hexdigest() == result.sha256
        with db._lock, db._connect() as sql:
            origin, = sql.execute(
                "SELECT origin_kind,identity_id,artifact_id,source_reference_id,"
                "parent_artifact_id,metadata_json FROM origin_links"
            ).fetchall()
            assert origin[:5] == ("derived", "A", result.artifact_id, None, aid)
            assert json.loads(origin[5])["source_placement_id"] == pids["A"]
            assert sql.execute("SELECT count(*) FROM source_references").fetchone() == (0,)
            assert sql.execute("SELECT count(*) FROM entitlements").fetchone() == (0,)
        summary = summarize_derived_journal(db)
        assert (summary["pending_count"], summary["committed_count"]) == (0, 1)
        before = db.placements()
        output_before = output.read_bytes()
        db.close()
        db = Storage(database)
        service.storage = db
        broker = broker_for_service()
        monkeypatch.setattr(transformer, "transform", lambda *_a, **_kw: pytest.fail("replay transformed again"))
        replay = broker.admit_transform(request, transformer_plugin_id=plugin)()
        assert replay.artifact_id == result.artifact_id
        assert db.placements() == before
        assert summarize_derived_journal(db) == summary
        with db._lock, db._connect() as sql:
            assert sql.execute("SELECT count(*) FROM origin_links").fetchone() == (1,)
        assert parent_a.read_bytes() == parent_b.read_bytes() == fixture.read_bytes() == original
        assert output.read_bytes() == output_before
    finally:
        db.close()
