from __future__ import annotations

import zipfile
from pathlib import Path

from cifereca.artifact_inspection import inspect_artifact
from cifereca.plugin_api import TransportService, check_service_contract


class GoodTransport:
    def matches(self, source):
        return True

    def fetch(self, source, destination):
        return Path(destination)


class BadTransport:
    def fetch(self, source, destination):
        return Path(destination)


def test_transport_contract_is_structural():
    assert isinstance(GoodTransport(), TransportService)
    assert check_service_contract("transport", GoodTransport()).ok
    check = check_service_contract("transport", BadTransport())
    assert not check.ok
    assert check.reason_code == "contract_mismatch"


def test_epub_inspection_requires_valid_container_rootfile(tmp_path):
    path = tmp_path / "bad.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            b'<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/missing.opf"/></rootfiles></container>',
        )
    result = inspect_artifact(path, "epub")
    assert not result.valid
