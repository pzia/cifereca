from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from cifereca.plugin_api import inspect_artifact_content, publish_artifact_content


def test_public_artifact_content_inspection_accepts_acsm():
    content = b'<?xml version="1.0"?><fulfillmentToken xmlns="http://ns.adobe.com/adept"><resourceItemInfo/></fulfillmentToken>'
    result = inspect_artifact_content(content, "acsm", strict_structure=True)
    assert result.valid
    assert result.media_format == "acsm"


def test_public_artifact_content_inspection_rejects_html_as_acsm():
    result = inspect_artifact_content(b"<html>login</html>", "acsm", strict_structure=True)
    assert not result.valid


def test_public_artifact_content_publication_is_atomic_and_replaces_destination():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp) / "book.acsm"
        destination.write_bytes(b"old")
        returned = publish_artifact_content(destination, b"new")
        assert returned == destination
        assert destination.read_bytes() == b"new"
        assert not list(destination.parent.glob(f".{destination.name}.*.part"))
