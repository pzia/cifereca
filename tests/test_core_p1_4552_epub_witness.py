"""Local exact EPUB witness: real parser, real SQLite, no network."""
from hashlib import sha256
from types import SimpleNamespace as NS
from threading import RLock
import json
import zipfile

from cifereca import epub_metadata
from cifereca.storage import Storage
from cifereca.core.library_projection import local_book_path
from cifereca.core.search_orchestration import capture_search_read_scope
from cifereca.core.notice_epub_witness import local_epub_witness, EpubWitnessProposal, PARSER
from cifereca.core.notice_parse_witness_store import read_approved_parse_witness
from cifereca.core.notice_parse_witness_alert import read_parse_witness_diagnostic
from tests.test_epub_cover_contract import _write_epub


def test_exact_local_epub_explicit_pin_restart_and_parser_regression(tmp_path, monkeypatch):
    path = tmp_path / "exact.epub"
    _write_epub(path, epub3=True)
    body = path.read_bytes()
    digest = sha256(body).hexdigest()
    aid = "sha256:" + digest
    identity = NS(identity_id="alice")
    other = NS(identity_id="bob")
    identities = {"alice": identity, "bob": other}
    config = NS(identities=lambda: [{"id": k, "directory_name": k} for k in identities],
                get=lambda *args: "")
    db_path = tmp_path / "witness.sqlite"
    def service(db):
        host = NS(storage=db, config=config, identities=list(identities.values()),
                  identity_by_id=identities.get, current_identity=identity,
                  library_dir=lambda: tmp_path, _source_context_lock=RLock(),
                  _source_context_epoch=0,
                  _notice_regression_capture_directory=tmp_path / "captures")
        host.local_book_path = lambda placement, **kwargs: local_book_path(host, placement, **kwargs)
        return host

    db = Storage(db_path)
    try:
        db.artifact_put({"artifact_id": aid, "sha256": digest, "size": len(body),
                         "media_format": "epub",
                         "metadata": epub_metadata.inspect_epub(path, metadata_only=True)})
        placement = db.placement_put({"artifact_id": aid, "identity_id": "alice",
                                      "path": str(path), "state": "present"})
        host = service(db)
        scope = capture_search_read_scope(host, "alice")
        proposal = local_epub_witness(host, placement_id=placement, scope=scope)
        assert type(proposal) is EpubWitnessProposal
        assert read_approved_parse_witness(db, key=proposal.key, parser_id=PARSER) is None
        pin = local_epub_witness(host, placement_id=placement, scope=scope, approved=proposal)
        assert pin.generation == 1 and pin.baseline.approved_input_sha256 == digest
        host.current_identity = other
        assert local_epub_witness(host, placement_id=placement,
                                  scope=capture_search_read_scope(host, "bob")) is None
        original_placement = db.placement_get(placement)
        original_artifact = db.artifact_get(aid)
    finally:
        db.close(final=True)
    original = epub_metadata.inspect_epub
    def drift(*args, **kwargs):
        result = original(*args, **kwargs)
        result["epub_metadata"].pop("title", None)
        return result
    monkeypatch.setattr(epub_metadata, "inspect_epub", drift)
    monkeypatch.setattr(epub_metadata, "SCHEMA", str(epub_metadata.SCHEMA) + ".1")
    db = Storage(db_path)
    try:
        host = service(db)
        scope = capture_search_read_scope(host, "alice")
        for _ in range(2):
            result = local_epub_witness(host, placement_id=placement, scope=scope)
            assert result.status == "parser_regression_confirmed"
            assert result.affected_fields == ("critical_field_1",)
        alert = read_parse_witness_diagnostic(db, key=proposal.key, parser_id=PARSER)
        assert alert["generation"] == 1 and alert["status"] == "parser_regression_confirmed"
        assert alert["capture_state"] == "partial"
        archive, = (tmp_path / "captures").glob("*.zip")
        with zipfile.ZipFile(archive) as z:
            manifest = json.loads(z.read("manifest.json"))
            assert manifest["omitted"] == ["response.raw"]
            assert "last_healthy.json" in z.namelist()
            assert "title" not in json.loads(z.read("current_parse.json"))
            assert "title" in json.loads(z.read("last_healthy.json"))
        assert not local_epub_witness(host, placement_id=placement, scope=scope,
                                      revalidate=True).can_revalidate
        assert read_approved_parse_witness(db, key=proposal.key, parser_id=PARSER) == pin
        assert db.placement_get(placement) == original_placement
        assert db.artifact_get(aid) == original_artifact
        assert path.read_bytes() == body
        assert db.entitlements() == db.acquisitions() == []
        # Explicitly review a changed but still valid parser result before
        # approving a replacement; never reset a degraded pin automatically.
        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            value["epub_metadata"]["title"] = "New reviewed title"
            return value
        monkeypatch.setattr(epub_metadata, "inspect_epub", changed)
        review = local_epub_witness(host, placement_id=placement, scope=scope)
        assert review.can_revalidate and review.status == "parser_regression_confirmed"
        renewal = local_epub_witness(host, placement_id=placement, scope=scope, revalidate=True)
        assert type(renewal) is EpubWitnessProposal and renewal.revalidation
        assert read_approved_parse_witness(db, key=proposal.key, parser_id=PARSER) == pin
        assert local_epub_witness(host, placement_id=placement, scope=scope, approved=renewal) is None
        replacement = local_epub_witness(host, placement_id=placement, scope=scope,
                                         approved=renewal, revalidate=True)
        assert replacement.generation == 2
        assert read_parse_witness_diagnostic(db, key=proposal.key, parser_id=PARSER) is None
    finally:
        db.close(final=True)


def test_normal_catalog_parse_keeps_shared_artifact_healthy_and_alerts_only_owner(tmp_path, monkeypatch):
    from cifereca import catalog
    from cifereca.diagnostics import Diagnostics
    from cifereca.core.notice_epub_witness import _local_key

    root = tmp_path / "library"
    for name in ("alice", "bob"):
        (root / name).mkdir(parents=True)
    path = root / "alice" / "exact.epub"
    _write_epub(path, epub3=True)
    body = path.read_bytes()
    other_path = root / "bob" / "same.epub"
    other_path.write_bytes(body)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    diagnostics = Diagnostics()
    db = Storage(tmp_path / "normal.sqlite")
    try:
        identities = {name: NS(identity_id=name) for name in ("alice", "bob")}
        for name in identities:
            db.identity_put({"identity_id": name, "label": name, "directory_name": name})
        index = catalog.LocalCatalog(db, root, regression_capture_directory=tmp_path / "captures",
                                     diagnostics=diagnostics)
        assert index.scan().cataloged == 2
        a = db.placement_for_path(path)
        b = db.placement_for_path(other_path)
        aid = a["artifact_id"]
        assert b["artifact_id"] == aid
        config = NS(identities=lambda: [{"id": k, "directory_name": k} for k in identities],
                    get=lambda *args: "")
        service = NS(storage=db, config=config, identities=list(identities.values()),
                     identity_by_id=identities.get, current_identity=identities["alice"],
                     library_dir=lambda: root, _source_context_lock=RLock(), _source_context_epoch=0)
        service.local_book_path = lambda placement, **kw: local_book_path(service, placement, **kw)
        scope = capture_search_read_scope(service, "alice")
        proposal = local_epub_witness(service, placement_id=a["placement_id"], scope=scope)
        pin = local_epub_witness(service, placement_id=a["placement_id"], scope=scope, approved=proposal)
        assert pin.generation == 1
        healthy = db.artifact_get(aid)["metadata"]
        parser = catalog.inspect_epub
        version = str(epub_metadata.SCHEMA) + ".1"
        def drift(*args, **kwargs):
            value = parser(*args, **kwargs)
            value["epub_metadata"].pop("title", None)
            value["epub_metadata_schema"] = version
            return value
        monkeypatch.setattr(catalog, "inspect_epub", drift)
        monkeypatch.setattr(catalog, "EPUB_METADATA_SCHEMA", version)
        # B's actual targeted refresh must not overwrite A's healthy shared
        # metadata nor create an alert in either profile from B's scope.
        assert index.index_path(other_path, identity_id="bob")["status"] == "indexed"
        assert db.artifact_get(aid)["metadata"]["epub_metadata"] == healthy["epub_metadata"]
        assert read_parse_witness_diagnostic(db, key=proposal.key, parser_id=PARSER) is None
        b_key = _local_key("bob", b["placement_id"], aid)
        assert read_parse_witness_diagnostic(db, key=b_key, parser_id=PARSER) is None
        for _ in range(2):
            assert index.scan(force_covers=True).cataloged == 2
            assert db.artifact_get(aid)["metadata"]["epub_metadata"] == healthy["epub_metadata"]
        alert = read_parse_witness_diagnostic(db, key=proposal.key, parser_id=PARSER)
        assert alert["status"] == "parser_regression_confirmed"
        assert alert["capture_state"] == "partial"
        assert read_parse_witness_diagnostic(db, key=b_key, parser_id=PARSER) is None
        assert len(list((tmp_path / "captures").glob("*.zip"))) == 1
        assert len([event for event in diagnostics.snapshot() if event["category"] == "NOTICE"]) == 1
        assert all(row["metadata"]["epub_metadata"]["title"] == healthy["epub_metadata"]["title"]
                   for row in db.catalog_entries())
        assert read_approved_parse_witness(db, key=proposal.key, parser_id=PARSER) == pin
        assert path.read_bytes() == other_path.read_bytes() == body
        assert db.acquisitions() == db.entitlements() == []
    finally:
        db.close(final=True)
