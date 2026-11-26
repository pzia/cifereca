"""#4980: real SQLite Library projection refuses ambiguous loan-to-Artifact joins.

The borrower observation and OriginLinks are synthetic; EPUB bytes, cover
extraction, persisted placements and the read-side projection are real.
"""
import hashlib
from io import BytesIO
from zipfile import ZipFile

from PIL import Image

from cifereca import epub_metadata
from cifereca.core import library_projection
from cifereca.core.book_cover_source_projection import project_exact_loan_cover
from cifereca.models import Loan
from tests.test_core_library_placement_identity_scope import _service
from tests.test_core_p0_2797_originlink_library_projection import _source_origin
from tests.test_core_p0_2797_origin_kind_projection import _loan_entitlement


def test_two_persisted_epub_origins_never_assign_loan_to_first_artifact(tmp_path, monkeypatch):
    cache = tmp_path / "isolated-cache"

    def cache_dir(*, create=True):
        if create:
            cache.mkdir(parents=True, exist_ok=True)
        return cache

    monkeypatch.setattr(epub_metadata, "cache_dir", cache_dir)
    service = _service(tmp_path)
    loan = Loan(
        id="loan-one", provider="Numilog", medium="digital",
        record_id="edition-a", title="Synthetic loan",
        source_provider="numilog", source_credential="card-a",
        source_account_id="buyer-a", identity_id="alice",
    )
    # Inject only the already-qualified borrower observation, not Library rows,
    # association proofs, storage reads or the cover resolver.
    monkeypatch.setattr(library_projection, "loan_display_rows",
                        lambda _service, identity: (loan,) if identity == "alice" else ())

    def add_epub(suffix, rgb):
        path = tmp_path / "alice" / (suffix + ".epub")
        path.parent.mkdir(parents=True, exist_ok=True)
        png = BytesIO()
        Image.new("RGB", (400, 600), rgb).save(png, format="PNG")
        with ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml",
                "<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
                "<rootfiles><rootfile full-path='OPS/content.opf'/></rootfiles></container>")
            archive.writestr("OPS/content.opf",
                "<package xmlns='http://www.idpf.org/2007/opf' version='3.0'>"
                "<metadata><dc:title xmlns:dc='http://purl.org/dc/elements/1.1/'>"
                "Synthetic edition</dc:title></metadata><manifest>"
                "<item id='cover' href='cover.png' media-type='image/png' "
                "properties='cover-image'/></manifest><spine/></package>")
            archive.writestr("OPS/cover.png", png.getvalue())
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        artifact = "sha256:" + digest
        metadata = epub_metadata.inspect_epub(path)
        url = epub_metadata.cached_cover_url(metadata)
        assert url
        service.storage.artifact_put({
            "artifact_id": artifact, "sha256": digest, "size": len(data),
            "media_format": "epub", "metadata": metadata,
        })
        placement = service.storage.placement_put({
            "artifact_id": artifact, "path": str(path), "identity_id": "alice",
            "provider_id": "local", "source_item_id": suffix, "state": "present",
            "metadata": {"placement_kind": "library"},
        })
        service.storage.catalog_entry_put({
            "artifact_id": artifact, "placement_id": placement,
            "identity_id": "alice", "provider_id": "local", "item_id": suffix,
            "title": "Synthetic edition", "local_state": "local", "media_format": "epub",
            "metadata": {**metadata, "path": str(path)},
        })
        _source_origin(service, identity="alice", provider="numilog",
                       credential="card-a", account="buyer-a", edition="edition-a",
                       artifact_id=artifact, token=suffix, kind="loan")
        return artifact, placement, url

    def snapshot():
        return tuple(service.library_entries(identity_id="alice", scan=False))

    try:
        first, first_placement, first_url = add_epub("first", (39, 154, 74))
        _loan_entitlement(service)
        rows = snapshot()
        assert len(rows) == 1
        decision = project_exact_loan_cover(loan, rows, revision=1, snapshot_complete=True)
        assert decision.status == "selected" and decision.uri == first_url
        assert decision.tier == "embedded"
        second, second_placement, second_url = add_epub("second", (170, 45, 82))
        assert first != second and first_url != second_url
        rows = snapshot()
        local = [row for row in rows if row["local"]]
        remote = [row for row in rows if not row["local"]]
        assert {row["artifact_id"] for row in local} == {first, second}
        assert {row["placement_id"] for row in local} == {first_placement, second_placement}
        assert {row["thumbnail_url"] for row in local} == {first_url, second_url}
        assert all(not row["possessions"] for row in local)
        assert all(not row.get("_exact_loan_cover_proofs") for row in local)
        unlinked, = remote
        assert unlinked["loan_id"] == loan.id and unlinked["artifact_id"] is None
        # There is no admitted association to either EPUB. This is missing
        # evidence at the join boundary, not N1's two-admitted-candidate ambiguity.
        for ordered in (rows, tuple(reversed(rows))):
            decision = project_exact_loan_cover(
                loan, ordered, revision=2, snapshot_complete=True,
            )
            assert decision.status == "missing" and not decision.uri
        assert len(service.storage.entitlements()) == 1
        assert service.storage.acquisitions() == []
    finally:
        service.close()
