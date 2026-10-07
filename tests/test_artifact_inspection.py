from __future__ import annotations

import zipfile

from cifereca.artifact_inspection import inspect_artifact


def _write_epub(path, *, mimetype=b"application/epub+zip", rootfile="OEBPS/content.opf", include_package=True):
    with zipfile.ZipFile(path, "w") as archive:
        if mimetype is not None:
            archive.writestr("mimetype", mimetype)
        archive.writestr(
            "META-INF/container.xml",
            (
                '<?xml version="1.0"?>'
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles>'
                f'<rootfile full-path="{rootfile}" media-type="application/oebps-package+xml"/>'
                '</rootfiles></container>'
            ),
        )
        if include_package:
            archive.writestr(rootfile, '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"></package>')


def test_valid_epub_package_is_accepted(tmp_path):
    path = tmp_path / "valid.epub"
    _write_epub(path)
    inspected = inspect_artifact(path, "epub", strict_structure=True)
    assert inspected.valid
    assert inspected.media_format == "epub"


def test_epub_missing_declared_package_is_rejected(tmp_path):
    path = tmp_path / "missing-package.epub"
    _write_epub(path, include_package=False)
    inspected = inspect_artifact(path, "epub", strict_structure=True)
    assert not inspected.valid
    assert inspected.reason == "epub_package_missing"


def test_epub_missing_mimetype_is_rejected(tmp_path):
    path = tmp_path / "missing-mimetype.epub"
    _write_epub(path, mimetype=None)
    inspected = inspect_artifact(path, "epub", strict_structure=True)
    assert not inspected.valid
    assert inspected.reason == "epub_mimetype_missing"


def test_epub_invalid_mimetype_is_rejected(tmp_path):
    path = tmp_path / "bad-mimetype.epub"
    _write_epub(path, mimetype=b"application/zip")
    inspected = inspect_artifact(path, "epub", strict_structure=True)
    assert not inspected.valid


def test_default_epub_inspection_remains_tolerant_for_fulfillment_artifacts(tmp_path):
    path = tmp_path / "legacy.epub"
    _write_epub(path, include_package=False)
    inspected = inspect_artifact(path, "epub")
    assert inspected.valid
    assert inspected.media_format == "epub"
