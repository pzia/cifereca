"""P0 #1143: EPUB metadata status is a closed code, never private parser prose.

Only synthetic ZIP files are parsed. No user book, DRM, provider or network.
"""
from __future__ import annotations

import json
import zipfile

import pytest

from cifereca import epub_metadata


SECRET = "https://private.invalid/LOAN/book?auth=PRIVATE_EPUB_TOKEN"


def _zip(tmp_path, content=None):
    path = tmp_path / "fixture.epub"
    with zipfile.ZipFile(path, "w") as archive:
        if content is not None:
            archive.writestr("META-INF/container.xml", content)
        else:
            archive.writestr("unrelated.txt", "hello")
    return path


@pytest.mark.parametrize(("container", "expected"), [
    (None, "ambiguous_or_missing_entry"),
    ("<!DOCTYPE foo><container/>", "unsafe_xml"),
])
def test_fixed_epub_parser_rejections_remain_compatible(tmp_path, container, expected):
    output = epub_metadata.inspect_epub(_zip(tmp_path, content=container))
    assert output["epub_status"] == expected
    assert output["epub_cover_status"] == "metadata_unreadable"
    assert output["epub_metadata"] == {}


def test_private_valueerror_message_cannot_be_stored_or_exported(
    tmp_path, monkeypatch,
):
    path = _zip(tmp_path, content="<container/>")
    monkeypatch.setattr(
        epub_metadata, "_xml",
        lambda _data: (_ for _ in ()).throw(ValueError(SECRET)),
    )
    output = epub_metadata.inspect_epub(path)
    assert output["epub_status"] == "unreadable_metadata"
    assert output["epub_cover_status"] == "metadata_unreadable"
    assert "PRIVATE_EPUB_TOKEN" not in json.dumps(output)
    assert "private.invalid" not in json.dumps(output)


def test_hostile_valueerror_subclass_is_never_stringified(tmp_path, monkeypatch):
    class HostileValueError(ValueError):
        def __str__(self):
            raise AssertionError("EPUB exception must never be stringified")
        def __repr__(self):
            raise AssertionError("EPUB exception must never be repr'd")
    path = _zip(tmp_path, content="<container/>")
    def fail(_data):
        raise HostileValueError(SECRET)
    monkeypatch.setattr(epub_metadata, "_xml", fail)
    output = epub_metadata.inspect_epub(path)
    assert output["epub_status"] == "unreadable_metadata"
    assert SECRET not in json.dumps(output)


def test_untrusted_valueerror_with_safe_prefix_does_not_bypass_exact_match(
    tmp_path, monkeypatch,
):
    path = _zip(tmp_path, content="<container/>")
    def fail(_data):
        raise ValueError("unsafe_xml: " + SECRET)
    monkeypatch.setattr(epub_metadata, "_xml", fail)
    output = epub_metadata.inspect_epub(path)
    assert output["epub_status"] == "unreadable_metadata"
    assert "PRIVATE_EPUB_TOKEN" not in repr(output)
