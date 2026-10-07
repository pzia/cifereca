from __future__ import annotations

from cifereca.plugin_api import inspect_artifact_content


def test_public_artifact_content_inspection_accepts_acsm():
    content = b'<?xml version="1.0"?><fulfillmentToken xmlns="http://ns.adobe.com/adept"><resourceItemInfo/></fulfillmentToken>'
    result = inspect_artifact_content(content, "acsm", strict_structure=True)
    assert result.valid
    assert result.media_format == "acsm"


def test_public_artifact_content_inspection_rejects_html_as_acsm():
    result = inspect_artifact_content(b"<html>login</html>", "acsm", strict_structure=True)
    assert not result.valid
