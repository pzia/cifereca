from __future__ import annotations

import zipfile

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_numilog import validate_epub


def _write_epub(path, *, opf="<package/>", extras=None):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        archive.writestr("OEBPS/content.opf", opf)
        for name, data in (extras or {}).items():
            archive.writestr(name, data)


def test_numilog_delegates_generic_structure_validation_to_core(tmp_path):
    path = tmp_path / "invalid.epub"
    _write_epub(path, opf="<metadata/>")

    with pytest.raises(ProviderError, match="EPUB valide"):
        validate_epub(path)


def test_numilog_keeps_provider_specific_lcp_rejection(tmp_path):
    path = tmp_path / "lcp.epub"
    _write_epub(path, extras={"META-INF/license.lcpl": "{}"})

    with pytest.raises(ProviderCapabilityError, match="protégé Adobe/LCP"):
        validate_epub(path)


def test_numilog_allows_standard_font_obfuscation(tmp_path):
    path = tmp_path / "font-obfuscation.epub"
    encryption = """<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
        xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
      <enc:EncryptedData>
        <enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
      </enc:EncryptedData>
    </encryption>"""
    _write_epub(path, extras={"META-INF/encryption.xml": encryption})

    validate_epub(path)


def test_numilog_rejects_non_font_encryption(tmp_path):
    path = tmp_path / "encrypted.epub"
    encryption = """<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
        xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
      <enc:EncryptedData>
        <enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes256-cbc"/>
      </enc:EncryptedData>
    </encryption>"""
    _write_epub(path, extras={"META-INF/encryption.xml": encryption})

    with pytest.raises(ProviderCapabilityError, match="chiffrement EPUB"):
        validate_epub(path)
