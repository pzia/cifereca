"""#3252: diagnose EPUB encryption markers without weakening Core's veto."""
from __future__ import annotations

from zipfile import ZipFile

import pytest

from cifereca.core.canonical_encryption_diagnostics import (
    rejected_epub_encryption_profile,
)


@pytest.mark.parametrize(("xml", "expected"), [
    (
        '<encryption><EncryptedData><EncryptionMethod '
        'Algorithm="http://www.w3.org/2001/04/xmlenc#aes256-cbc"/>'
        '</EncryptedData></encryption>',
        "aes_algorithm",
    ),
    (
        '<encryption><EncryptedData><EncryptionMethod '
        'Algorithm="http://www.idpf.org/2008/embedding"/>'
        '</EncryptedData></encryption>',
        "font_obfuscation_algorithms_only",
    ),
    (
        '<encryption xmlns="http://www.w3.org/2001/04/xmlenc#">'
        '<EncryptedData><EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
        '</EncryptedData></encryption>',
        "font_obfuscation_algorithms_only",
    ),
    (
        '<encryption xmlns="http://www.w3.org/2001/04/xmlenc#">'
        '<EncryptedData /></encryption>',
        "generic_xmlenc_marker",
    ),
    (
        '<encryption><marker>adobe.com/adept</marker></encryption>',
        "adobe_adept_marker",
    ),
    (
        '<encryption><EncryptedData><EncryptionMethod '
        'Algorithm="urn:unknown:protection"/></EncryptedData></encryption>',
        "other_encryption_marker",
    ),
    ("<encryption", "encryption_manifest_malformed"),
])
def test_closed_encryption_profiles(tmp_path, xml, expected):
    path = tmp_path / "candidate.epub"
    with ZipFile(path, "w") as archive:
        archive.writestr("META-INF/encryption.xml", xml)
    assert rejected_epub_encryption_profile(path) == expected


def test_missing_and_oversized_manifest_classify_without_xml_leak(tmp_path):
    path = tmp_path / "candidate.epub"
    with ZipFile(path, "w") as archive:
        archive.writestr("other", "safe")
    assert rejected_epub_encryption_profile(path) == "encryption_manifest_missing"
    with ZipFile(path, "w") as archive:
        archive.writestr("META-INF/encryption.xml", "PRIVATE_SECRET" * 30000)
    result = rejected_epub_encryption_profile(path)
    assert result == "encryption_manifest_oversized"
    assert "PRIVATE_SECRET" not in result
