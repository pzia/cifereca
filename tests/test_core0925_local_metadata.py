from __future__ import annotations

import zipfile
from pathlib import Path

from cifereca.descriptions import description_text
from cifereca.epub_metadata import inspect_epub


def _epub(path: Path) -> Path:
    container = """<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles>
    </container>"""
    opf = """<?xml version="1.0" encoding="utf-8"?>
    <package xmlns="http://www.idpf.org/2007/opf"
             xmlns:dc="http://purl.org/dc/elements/1.1/"
             version="3.0">
      <metadata>
        <dc:title>Titre local</dc:title>
        <dc:creator id="author" role="aut" file-as="Auteur, Alice">Alice Auteur</dc:creator>
        <dc:contributor id="translator">Bob Traducteur</dc:contributor>
        <meta refines="#translator" property="role">trl</meta>
        <dc:identifier id="isbn" scheme="ISBN">9781234567890</dc:identifier>
        <dc:publisher>Maison</dc:publisher>
        <dc:date>2026-01-02</dc:date>
        <dc:language>fr</dc:language>
        <dc:subject>Roman</dc:subject>
        <dc:description>&lt;p&gt;Résumé &lt;strong&gt;riche&lt;/strong&gt;.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Un&lt;/li&gt;&lt;li&gt;Deux&lt;/li&gt;&lt;/ul&gt;</dc:description>
        <dc:rights>CC BY</dc:rights>
        <meta name="calibre:series" content="Cycle"/>
        <meta name="calibre:series_index" content="2"/>
      </metadata>
      <manifest/>
      <spine/>
    </package>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OPS/content.opf", opf)
    return path


def test_epub_metadata_extracts_local_notice_and_preserves_provenance(tmp_path: Path):
    result = inspect_epub(_epub(tmp_path / "book.epub"))

    assert result["epub_status"] == "ok"
    meta = result["epub_metadata"]
    assert meta["title"] == "Titre local"
    assert meta["authors"] == ["Alice Auteur"]
    assert meta["contributors"][0]["name"] == "Alice Auteur"
    assert meta["contributors"][0]["role"] == "aut"
    assert any(row["name"] == "Bob Traducteur" and row["role"] == "trl" for row in meta["contributors"])
    assert meta["identifiers"] == [{"value": "9781234567890", "scheme": "ISBN", "source": "epub"}]
    assert meta["publishers"] == ["Maison"]
    assert meta["dates"] == ["2026-01-02"]
    assert meta["languages"] == ["fr"]
    assert meta["subjects"] == ["Roman"]
    assert meta["rights"] == ["CC BY"]
    assert meta["series"] == [{"name": "Cycle", "index": "2"}]
    assert meta["source"] == "epub"
    assert meta["opf"] == "OPS/content.opf"

    original = meta["original"]
    creator = next(row for row in original if row["name"] == "creator")
    assert creator["value"] == "Alice Auteur"
    assert creator["attributes"]["role"] == "aut"
    assert creator["attributes"]["file-as"] == "Auteur, Alice"


def test_epub_description_is_plain_readable_text(tmp_path: Path):
    result = inspect_epub(_epub(tmp_path / "book.epub"))
    text = result["epub_metadata"]["description"]

    assert "<p>" not in text
    assert "<strong>" not in text
    assert "Résumé riche." in text
    assert "• Un" in text
    assert "• Deux" in text


def test_description_normalization_preserves_paragraphs_lists_and_entities():
    raw = "<p>Un &amp; deux</p><p>Suite</p><ul><li>A</li><li>B</li></ul><script>secret()</script>"
    text = description_text(raw)

    assert text == "Un & deux\n\nSuite\n\n• A\n• B"
    assert "secret" not in text


def test_metadata_inspection_is_content_local_and_network_free(tmp_path: Path, monkeypatch):
    path = _epub(tmp_path / "book.epub")

    def fail(*args, **kwargs):
        raise AssertionError("metadata inspection must not use network")

    import socket
    monkeypatch.setattr(socket, "create_connection", fail)

    result = inspect_epub(path)
    assert result["epub_status"] == "ok"
    assert result["epub_metadata"]["source"] == "epub"
