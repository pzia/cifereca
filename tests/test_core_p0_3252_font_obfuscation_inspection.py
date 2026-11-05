"""#3252: namespace is not DRM; exact font obfuscation is non-content DRM.

Real EPUB ZIPs; both public inspection entry points must agree. Every
mixed, malformed or unknown encryption method remains protected.
"""
from __future__ import annotations

import io
from pathlib import Path
from zipfile import ZipFile

import pytest

from cifereca.artifact_inspection import inspect_artifact, inspect_artifact_content


FONT = "http://www.idpf.org/2008/embedding"
ADOBE_FONT = "http://ns.adobe.com/pdf/enc#RC"
AES = "http://www.w3.org/2001/04/xmlenc#aes256-cbc"
ENVELOPE = (
    '<encryption xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
    '{data}</encryption>'
)


def _entry(algorithm: str) -> str:
    return ('<enc:EncryptedData><enc:EncryptionMethod '
            f'Algorithm="{algorithm}"/></enc:EncryptedData>')


def _epub(encryption: str | None) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        if encryption is not None:
            archive.writestr("META-INF/encryption.xml", encryption)
    return buffer.getvalue()


@pytest.mark.parametrize(("manifest", "protected"), [
    (None, False),
    (ENVELOPE.format(data=_entry(FONT)), False),
    (ENVELOPE.format(data=_entry(ADOBE_FONT)), False),
    (ENVELOPE.format(data=_entry(FONT) + _entry(ADOBE_FONT)), False),
    (ENVELOPE.format(data=_entry(AES)), True),
    (ENVELOPE.format(data=_entry(FONT) + _entry(AES)), True),
    (ENVELOPE.format(data=_entry(FONT) + _entry("urn:unknown")), True),
    (ENVELOPE.format(data="<enc:EncryptedData/>"), True),
    (ENVELOPE.format(data=_entry(FONT) + "<enc:EncryptionMethod Algorithm='urn:other'/>"), True),
    (ENVELOPE.format(data=_entry(FONT) + "<enc:EncryptedKey/>"), True),
    (ENVELOPE.format(data=_entry(FONT) + "<enc:KeyInfo/>"), True),
    (ENVELOPE.format(data=_entry(FONT) + "<enc:CipherValue/>"), True),
    (ENVELOPE.format(data=_entry(FONT) + "<custom:Unknown xmlns:custom='urn:test'/>"), True),
    ("<encryption", True),
    ("<encryption/>", True),
    ("<invalid>" + _entry(FONT) + "</invalid>", True),
    (ENVELOPE.format(data=_entry(FONT)) + " " * 270000, True),
])
def test_font_only_manifest_does_not_trip_core_protection_veto(
    tmp_path: Path, manifest: str | None, protected: bool,
):
    content = _epub(manifest)
    path = tmp_path / "candidate.epub"
    path.write_bytes(content)
    on_disk = inspect_artifact(path, expected_format="epub")
    in_memory = inspect_artifact_content(content, expected_format="epub")
    assert on_disk.valid and in_memory.valid
    assert on_disk.media_format == in_memory.media_format == "epub"
    assert on_disk.protected is protected
    assert in_memory.protected is protected


def test_duplicate_encryption_manifest_cannot_hide_aes_behind_font_only(tmp_path):
    from warnings import catch_warnings, simplefilter
    buffer = io.BytesIO()
    with catch_warnings():
        simplefilter("ignore", UserWarning)
        with ZipFile(buffer, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", "<container/>")
            archive.writestr("META-INF/encryption.xml",
                             ENVELOPE.format(data=_entry(AES)))
            archive.writestr("META-INF/encryption.xml",
                             ENVELOPE.format(data=_entry(FONT)))
    path = tmp_path / "duplicate.epub"
    path.write_bytes(buffer.getvalue())
    assert inspect_artifact(path).protected is True
    assert inspect_artifact_content(buffer.getvalue()).protected is True
