"""P1 #2854: USB byte inspection must never become a transfer permission."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from cifereca.core.device_usb_content_review import (
    UsbContentReview, review_usb_content,
)
from cifereca.core.device_usb_preview import UsbLocalArtifact


def epub_bytes(*, encrypted=False):
    sink = BytesIO()
    with ZipFile(sink, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/package.opf"/></rootfiles>'
            "</container>",
        )
        archive.writestr("OEBPS/package.opf", "<package/>")
        if encrypted:
            archive.writestr("META-INF/encryption.xml", "<EncryptedData>AES</EncryptedData>")
    return sink.getvalue()


def pdf_bytes(*, encrypted=False):
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog "
        + (b"/Encrypt true " if encrypted else b"")
        + b">>\nendobj\n%%EOF\n"
    )


def case(fmt="epub", *, data=None):
    if data is None:
        data = pdf_bytes() if fmt == "pdf" else epub_bytes()
    digest = sha256(data).hexdigest()
    return dict(
        identity_id="alice",
        artifact={
            "artifact_id": "sha256:" + digest, "sha256": digest,
            "size": len(data), "kind": "file", "media_format": fmt,
            "metadata": {},
        },
        local=UsbLocalArtifact("alice", digest, verified=True),
        content=data, selected_format=fmt,
    )


@pytest.mark.parametrize("fmt,observed,reason", [
    ("epub", "epub", "epub_drm_and_transfer_rights_unverified"),
    ("kepub", "epub", "kepub_semantics_drm_and_transfer_rights_unverified"),
    ("fxl.kepub", "epub", "kepub_semantics_drm_and_transfer_rights_unverified"),
    ("pdf", "pdf", "pdf_drm_and_transfer_rights_unverified"),
])
def test_well_formed_bytes_are_still_non_authorizing(fmt, observed, reason):
    values = case(fmt)
    before = deepcopy(values["artifact"])
    result = review_usb_content(**values)
    assert result == UsbContentReview("review", reason, observed, False)
    assert values["artifact"] == before
    assert result.executable is False


@pytest.mark.parametrize("fmt", ["epub", "kepub", "fxl.kepub"])
def test_explicit_epub_encryption_detected_and_blocked(fmt):
    values = case(fmt, data=epub_bytes(encrypted=True))
    result = review_usb_content(**values)
    assert result.status == "blocked"
    assert result.reason == "content_protection_detected"
    assert result.executable is False


@pytest.mark.parametrize("fmt", ["epub", "kepub", "fxl.kepub", "pdf"])
def test_unprotected_looking_content_never_proves_copy_right(fmt):
    values = case(fmt)
    values["artifact"]["metadata"] = {
        "protected": False, "drm_status": "none", "post_processed": True,
        "source_format": "epub",
    }
    r = review_usb_content(**values)
    assert r.status == "review"
    assert "rights_unverified" in r.reason
    assert not r.executable


@pytest.mark.parametrize("name,value", [
    ("protected", True), ("artifact_protected", True),
    ("drm_status", "encrypted"), ("drm_status", "protected"),
    ("drm_scheme", "lcp"), ("drm_scheme", "kdrm"),
    ("drm_scheme", "adept"),
])
def test_reported_drm_is_blocked_even_if_bytes_are_plain(name, value):
    values = case()
    values["artifact"]["metadata"][name] = value
    result = review_usb_content(**values)
    assert result.reason == "content_protection_reported"
    assert result.executable is False


@pytest.mark.parametrize("name,value", [
    ("drm_status", []), ("drm_status", {}),
    ("drm_status", None), ("drm_status", 1),
    ("drm_scheme", []), ("drm_scheme", {}),
    ("drm_scheme", None), ("drm_scheme", True),
])
def test_malformed_drm_descriptor_fails_closed_without_exception(name, value):
    values = case()
    values["artifact"]["metadata"][name] = value
    report = review_usb_content(**values)
    assert report.status == "blocked"
    assert report.reason == "content_protection_unverified"
    assert report.executable is False


@pytest.mark.parametrize("metadata", [None, [], "raw", True])
def test_malformed_artifact_metadata_fails_closed_without_exception(metadata):
    values = case()
    values["artifact"]["metadata"] = metadata
    report = review_usb_content(**values)
    assert report.status == "blocked"
    assert report.reason == "content_protection_unverified"
    assert report.executable is False


def test_pdf_encryption_indication_is_not_mistaken_for_drm_free():
    values = case("pdf", data=pdf_bytes(encrypted=True))
    result = review_usb_content(**values)
    assert result.status == "review"
    assert result.reason == "pdf_drm_and_transfer_rights_unverified"
    assert not result.executable


@pytest.mark.parametrize("fmt,body", [
    ("epub", pdf_bytes()),
    ("kepub", pdf_bytes()),
    ("fxl.kepub", pdf_bytes()),
    ("pdf", epub_bytes()),
    ("epub", b"broken-archive-data"),
    ("pdf", b"%PDF-1.4 no final marker"),
    ("epub", b"PK" + b"\x00" * 60),
], ids=[
    "epub-content-is-pdf",
    "kepub-content-is-pdf",
    "fxl-kepub-content-is-pdf",
    "pdf-content-is-epub",
    "epub-corrupt-archive",
    "pdf-truncated-marker",
    "epub-invalid-zip-header",
])
def test_wrong_format_or_malformed_structure_is_refused(fmt, body):
    values = case(fmt, data=body)
    result = review_usb_content(**values)
    assert result.reason == "content_structure_unverified"
    assert result.status == "blocked"


@pytest.mark.parametrize("change", [
    {"identity_id": "bob"}, {"local": None},
    {"local": UsbLocalArtifact("bob", "a" * 64, True)},
    {"local": UsbLocalArtifact("alice", "a" * 64, False)},
    {"selected_format": "acsm"}, {"selected_format": "EPUB"},
    {"max_bytes": True}, {"max_bytes": 0},
    {"max_bytes": 64 * 1024 * 1024 + 1},
])
def test_invalid_scope_or_request_never_grants_transfer(change):
    values = case()
    values.update(change)
    assert review_usb_content(**values).executable is False
    assert review_usb_content(**values).status == "blocked"


@pytest.mark.parametrize("key,value", [
    ("kind", "bundle"), ("kind", None), ("size", 0), ("size", True),
    ("sha256", "b" * 64), ("artifact_id", "sha256:" + "b" * 64),
    ("media_format", "pdf"), ("media_format", "acsm"),
])
def test_unverified_artifact_coordinates_refused(key, value):
    values = case()
    values["artifact"][key] = value
    result = review_usb_content(**values)
    assert result.status == "blocked"
    assert result.reason == "content_source_unverified"


def test_content_tampering_detected_even_if_previous_local_sha_was_attested():
    values = case()
    original = values["content"]
    values["content"] = bytes((original[0] ^ 1,)) + original[1:]
    result = review_usb_content(**values)
    assert result.reason == "content_bytes_unverified"


@pytest.mark.parametrize("body", [bytearray(b"a"), memoryview(b"a"), "a", b""])
def test_untrusted_content_container_or_truncation_refused(body):
    values = case()
    values["content"] = body
    result = review_usb_content(**values)
    assert result.status == "blocked"


def test_source_sha_mismatch_is_blocked_even_for_equal_length_document():
    values = case()
    values["artifact"]["sha256"] = "e" * 64
    values["artifact"]["artifact_id"] = "sha256:" + "e" * 64
    values["local"] = UsbLocalArtifact("alice", "e" * 64, True)
    result = review_usb_content(**values)
    assert result.reason == "content_bytes_unverified"


def test_content_size_budget_refuses_without_parsing_archive():
    values = case()
    values["max_bytes"] = 10
    result = review_usb_content(**values)
    assert result.reason == "content_source_unverified"
    assert result.executable is False


def test_compressed_epub_bomb_is_blocked_before_expansion():
    sink = BytesIO()
    with ZipFile(sink, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OEBPS/package.opf"/>'
            '</rootfiles></container>',
        )
        archive.writestr("OEBPS/package.opf", "<package/>")
        archive.writestr("OEBPS/massive.html", b"x" * (2 * 1024 * 1024))
    values = case("epub", data=sink.getvalue())
    assert values["artifact"]["size"] <= 16 * 1024
    values["max_bytes"] = 16 * 1024
    report = review_usb_content(**values)
    assert report.reason == "content_archive_unbounded"
    assert report.status == "blocked"
    assert report.executable is False


def test_safely_compressed_epub_within_budget_is_still_non_authorizing():
    values = case()
    values["max_bytes"] = 16 * 1024
    report = review_usb_content(**values)
    assert report.status == "review"
    assert report.executable is False
