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
