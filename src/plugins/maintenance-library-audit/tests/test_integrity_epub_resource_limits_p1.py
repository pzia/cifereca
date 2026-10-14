"""#1495: integrity scanning must not decompress attacker-sized EPUB entries.

ZIP central-directory claims are untrusted, even when the .epub on disk is
tiny. Tests create only tiny local fixtures, no external files or network.
"""
from __future__ import annotations

import struct
import zipfile

import pytest

import cifereca_library_audits as audit
from tests.test_integrity_integration import (
    Control, _make_integrity_tool, write_valid_epub,
)


def _claim_huge_member(path):
    """Forge one ZIP header's declared uncompressed size without large bytes."""
    blob = bytearray(path.read_bytes())
    signature = blob.index(b"PK\x01\x02")
    struct.pack_into("<I", blob, signature + 24, 0x7FFFFFFF)
    path.write_bytes(blob)


def test_forged_2_gib_zip_claim_is_rejected_without_decompression(
    monkeypatch, tmp_path,
):
    book = tmp_path / "PRIVATE_BOOK.epub"
    write_valid_epub(book)
    assert audit._file_integrity_issues(book, "epub") == ()
    _claim_huge_member(book)

    def must_not_decompress(*_args, **_kwargs):
        raise AssertionError("archive_limit must be checked before testzip")

    with monkeypatch.context() as patch:
        patch.setattr(zipfile.ZipFile, "testzip", must_not_decompress)
        assert audit._file_integrity_issues(book, "epub") == ("archive_limit",)

    tool, plan = _make_integrity_tool(tmp_path, [{
        "identity_id": "id1", "local": True,
        "local_path": str(book), "title": "PRIVATE_BOOK_TITLE",
        "media_format": "epub",
    }])
    control = Control()
    result = tool.run(plan, control)
    assert result.ok is False
    assert result.facts["checked"] == 1
    assert result.facts["invalid"] == 1
    assert result.facts["issues"] == {"archive_limit": 1}
    assert result.warnings == ("Fichier 1/1 : archive_limit",)
    assert [(event.get("current"), event.get("total"))
            for event in control.events] == [(0, 1), (1, 1)]
    assert "PRIVATE" not in str(result.warnings) + result.summary


@pytest.mark.parametrize("budget", ["members", "individual", "total"])
def test_budget_enforced_before_crc_or_large_mimetype_read(
    monkeypatch, tmp_path, budget,
):
    book = tmp_path / "PRIVATE_BUDGET.epub"
    write_valid_epub(book)
    assert audit._file_integrity_issues(book) == ()

    with monkeypatch.context() as patch:
        if budget == "members":
            patch.setattr(audit, "_MAX_EPUB_MEMBERS", 2)
        elif budget == "individual":
            patch.setattr(audit, "_MAX_EPUB_MEMBER_BYTES", 24)
        else:
            patch.setattr(audit, "_MAX_EPUB_UNCOMPRESSED_BYTES", 40)
        patch.setattr(zipfile.ZipFile, "testzip", lambda *_: pytest.fail(
            "ZIP payload must not be decompressed after budget rejection"
        ))
        assert audit._file_integrity_issues(book, "epub") == ("archive_limit",)

    assert audit._file_integrity_issues(book, "epub") == ()


def test_invalid_epub_still_reports_container_invalid_not_sensitive_zip_name(
    tmp_path,
):
    book = tmp_path / "PRIVATE_INVALID.zip"
    book.write_bytes(b"PRIVATE_NOT_A_ZIP")
    issues = audit._file_integrity_issues(book, "epub")
    assert issues == ("container_invalid",)
    assert "PRIVATE" not in str(issues)
