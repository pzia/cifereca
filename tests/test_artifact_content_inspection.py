from __future__ import annotations

import io
import zipfile

from cifereca.plugin_api import inspect_artifact_content


def _epub_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            b'<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        archive.writestr("OEBPS/content.opf", b"<package/>")
    return output.getvalue()


def test_inspect_artifact_content_accepts_valid_acsm():
    content = b'<?xml version="1.0"?><fulfillmentToken xmlns="http://ns.adobe.com/adept"><resourceItemInfo/></fulfillmentToken>'
    result = inspect_artifact_content(content, "acsm", strict_structure=True)
    assert result.valid
    assert result.media_format == "acsm"


def test_inspect_artifact_content_rejects_html_as_acsm():
    result = inspect_artifact_content(b"<html>login</html>", "acsm", strict_structure=True)
    assert not result.valid


def test_inspect_artifact_content_keeps_paris_pdf_truncation_guard():
    result = inspect_artifact_content(b"%PDF-1.7\nmissing eof", "pdf", strict_structure=True)
    assert not result.valid
    assert result.reason == "pdf_eof_missing"


def test_inspect_artifact_content_strict_epub_structure():
    result = inspect_artifact_content(_epub_bytes(), "epub", strict_structure=True)
    assert result.valid
    assert result.media_format == "epub"
