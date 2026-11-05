"""#3252: real EPUB bytes through Core's final selected-delivery boundary.

The final veto is exercised with genuine ZIP/EPUB files, not mocked
inspection/catalog predicates. No Kobo HTTP, token, runtime or user data.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from zipfile import ZipFile, ZIP_STORED

import pytest

from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs
from tests.test_core_p0_3171_selected_candidate_finalization import _prepare


@pytest.fixture(autouse=True)
def _isolate_delivery_lock(monkeypatch, tmp_path):
    lock = lambda: tmp_path / "state" / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_delivery.acquisition_lock_file", lock,
    )
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_offers.acquisition_lock_file", lock,
    )


def _epub_bytes(*, encrypted: bool) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf"'
            ' media-type="application/oebps-package+xml"/></rootfiles></container>'
        ))
        archive.writestr("OEBPS/content.opf", (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"'
            ' unique-identifier="book-id"><metadata'
            ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="book-id">synthetic-3252</dc:identifier>'
            '<dc:title>Synthetic fixture</dc:title>'
            '<dc:language>fr</dc:language></metadata>'
            '<manifest/><spine/></package>'
        ))
        if encrypted:
            archive.writestr("META-INF/encryption.xml", (
                '<?xml version="1.0"?><encryption>'
                '<EncryptedData><EncryptionMethod Algorithm="aes-256-cbc"/>'
                '</EncryptedData></encryption>'
            ))
    return buffer.getvalue()


@pytest.mark.parametrize("encrypted", [False, True], ids=["readable", "protected"])
def test_real_epub_bytes_keep_core_final_veto_and_attribution(
    rig, caplog, encrypted,
):
    service, db, root = rig
    _prepare(service)
    bytes_in = _epub_bytes(encrypted=encrypted)
    calls = []

    def candidate(_account, *, target_dir, **_kwargs):
        calls.append("candidate")
        path = Path(target_dir) / "candidate.epub"
        path.write_bytes(bytes_in)
        return path, ""

    service.session.rights_selected_delivery_candidate = candidate
    with caplog.at_level(
        logging.WARNING, logger="cifereca.core.canonical_selected_delivery",
    ):
        outcome = synchronize_account_source(service, service.account)
    assert calls == ["candidate"]
    execution = outcome["canonical_execution"]
    rejected = [
        entry.getMessage() for entry in caplog.records
        if entry.getMessage().startswith("TRACE canonical delivery final rejected ")
    ]
    if encrypted:
        assert execution["failed"] == 1
        assert execution["downloaded"] == 0
        assert len(rejected) == 1
        assert "inspection_valid=True" in rejected[0]
        assert "inspection_format=epub" in rejected[0]
        assert "inspection_protected=True" in rejected[0]
        assert "encryption_profile=aes_algorithm" in rejected[0]
        assert "catalog_valid=True" in rejected[0]
        assert "edition_id='edition'" in rejected[0]
        assert "right_id='purchase'" in rejected[0]
        assert refs(db)[1] == 0 and db.placements() == []
        assert not list((root / "A" / ".canonical-delivery").glob("run-*"))
    else:
        assert execution["downloaded"] == 1
        assert not rejected
        assert refs(db)[1] == 1
        placements = db.placements()
        assert len(placements) == 1
        assert Path(placements[0]["path"]).read_bytes() == bytes_in
        again = synchronize_account_source(service, service.account)
        assert again["canonical_execution"]["present_attested"] == 1
        assert calls == ["candidate"]


def test_font_only_xmlenc_namespace_epub_is_receivable_without_drm_bypass(rig):
    """Only standard font obfuscation is readable; Core still owns B."""
    service, db, root = rig
    _prepare(service)
    source = io.BytesIO(_epub_bytes(encrypted=False))
    with ZipFile(source, "a") as archive:
        archive.writestr("META-INF/encryption.xml", (
            '<encryption xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
            '<enc:EncryptedData><enc:EncryptionMethod '
            'Algorithm="http://www.idpf.org/2008/embedding"/>'
            '</enc:EncryptedData></encryption>'
        ))
    payload = source.getvalue()
    calls = []

    def candidate(_account, *, target_dir, **_kwargs):
        calls.append("get")
        path = Path(target_dir) / "font-only.epub"
        path.write_bytes(payload)
        return path, ""

    service.session.rights_selected_delivery_candidate = candidate
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert refs(db)[1] == 1
    assert Path(db.placements()[0]["path"]).read_bytes() == payload
    again = synchronize_account_source(service, service.account)
    assert again["canonical_execution"]["present_attested"] == 1
    assert calls == ["get"]
