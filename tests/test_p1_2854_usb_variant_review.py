"""P1 #2854: static format review is always advisory, with closed refusals."""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.core.device_usb_preview import UsbLocalArtifact
from cifereca.core.device_usb_variant_review import (
    UsbReaderFormatDeclaration, UsbVariantReview, review_usb_variant,
)


SHA = "a" * 64


def fixture(variant="epub", *, identity="alice"):
    device = {
        "identity_id": identity, "device_id": "reader-1", "enabled": True,
        "target_directory": "Books/Cifereca",
        "metadata": {"usb_fingerprint": "uuid-alice-0001"},
    }
    artifact = {
        "artifact_id": "sha256:" + SHA, "sha256": SHA, "size": 37,
        "media_format": variant, "kind": "file", "metadata": {},
    }
    source = UsbLocalArtifact(identity, SHA, verified=True)
    profile = UsbReaderFormatDeclaration(
        identity, "reader-1", "uuid-alice-0001",
        ("epub", "kepub", "fxl.kepub", "pdf"),
    )
    suffix = {"epub": ".epub", "kepub": ".kepub.epub",
              "fxl.kepub": ".fxl.kepub.epub", "pdf": ".pdf"}[variant]
    return dict(
        identity_id=identity, binding=device, artifact=artifact, local=source,
        selected_format=variant, target_relative_path="Books/Cifereca/book" + suffix,
        reader=profile,
    )


@pytest.mark.parametrize("variant", ["epub", "kepub", "fxl.kepub", "pdf"])
def test_static_variant_match_is_not_a_permission(variant):
    values = fixture(variant)
    before = deepcopy(values["binding"]), deepcopy(values["artifact"])
    result = review_usb_variant(**values)
    assert result == UsbVariantReview("review", "drm_and_transfer_rights_unverified")
    assert result.executable is False
    assert (values["binding"], values["artifact"]) == before


@pytest.mark.parametrize("suffix", [
    ".kepub.epub", ".fxl.kepub.epub", ".pdf", ".EPUB", ".epub/../b.epub",
])
def test_plain_epub_cannot_be_silently_renamed_into_another_variant(suffix):
    values = fixture()
    values["target_relative_path"] = "Books/Cifereca/book" + suffix
    assert review_usb_variant(**values).status == "blocked"


@pytest.mark.parametrize("variant,filename", [
    ("kepub", "book.epub"), ("kepub", "book.fxl.kepub.epub"),
    ("fxl.kepub", "book.kepub.epub"), ("pdf", "book.epub"),
])
def test_each_selected_variant_needs_its_exact_suffix(variant, filename):
    values = fixture(variant)
    values["target_relative_path"] = "Books/Cifereca/" + filename
    assert review_usb_variant(**values).reason == "target_variant_suffix_mismatch"


@pytest.mark.parametrize("key,value", [
    ("protected", True), ("artifact_protected", True),
    ("drm_status", "encrypted"), ("drm_scheme", "lcp"),
    ("drm_scheme", "adept"), ("drm_scheme", "kdrm"),
])
def test_explicit_protection_warning_is_refused(key, value):
    values = fixture()
    values["artifact"]["metadata"][key] = value
    assert review_usb_variant(**values).reason == "protection_reported"


@pytest.mark.parametrize("metadata", [
    {}, {"protected": False}, {"artifact_protected": False},
    {"drm_status": "none"}, {"post_processed": True},
    {"source_format": "EPUB"}, {"drm_scheme": "none"},
])
def test_file_metadata_never_grants_drm_or_copy_rights(metadata):
    values = fixture()
    values["artifact"]["metadata"] = metadata
    result = review_usb_variant(**values)
    assert result.status == "review"
    assert result.executable is False
    assert "rights_unverified" in result.reason


def test_cross_profile_same_sha_is_blocked():
    values = fixture()
    values["local"] = UsbLocalArtifact("bob", SHA, True)
    assert review_usb_variant(**values).reason == "owned_source_unverified"
    values = fixture()
    values["reader"] = UsbReaderFormatDeclaration("bob", "reader-1", "uuid-alice-0001", ("epub",))
    assert review_usb_variant(**values).reason == "reader_formats_unconfigured"
    values = fixture()
    values["binding"]["identity_id"] = "bob"
    assert review_usb_variant(**values).reason == "reader_scope_unverified"


@pytest.mark.parametrize("value", [
    None, False, ["epub"], (), ("epub", "epub"), ("epub", "lcp"),
    ([],), ("epub", None),
])
def test_unverified_device_formats_cannot_authorize_a_copy(value):
    values = fixture()
    values["reader"] = (
        UsbReaderFormatDeclaration("alice", "reader-1", "uuid-alice-0001", value)
        if value is not None else None
    )
    assert review_usb_variant(**values).reason == "reader_formats_unconfigured"


def test_declared_unsupported_format_is_refused():
    values = fixture("pdf")
    values["reader"] = UsbReaderFormatDeclaration("alice", "reader-1", "uuid-alice-0001", ("epub",))
    assert review_usb_variant(**values).reason == "reader_format_unsupported"


@pytest.mark.parametrize("change,expected", [
    ({"media_format": "EPUB"}, "selected_variant_unverified"),
    ({"media_format": "acsm"}, "selected_variant_unverified"),
    ({"kind": "bundle"}, "owned_source_unverified"),
    ({"sha256": "b" * 64}, "owned_source_unverified"),
    ({"size": 0}, "owned_source_unverified"),
    ({"artifact_id": "sha256:" + "b" * 64}, "owned_source_unverified"),
])
def test_untrusted_variant_metadata_cannot_claim_source(change, expected):
    values = fixture()
    values["artifact"].update(change)
    assert review_usb_variant(**values).reason == expected


@pytest.mark.parametrize("path", [
    None, "", "/Books/Cifereca/b.epub", "Books/Other/b.epub",
    "Books/Cifereca/../a.epub", "Books/Cifereca//a.epub",
    "Books/Cifereca/\\file.epub", "Books/Cifereca",
])
def test_invalid_managed_target_rejected(path):
    values = fixture()
    values["target_relative_path"] = path
    assert review_usb_variant(**values).status == "blocked"


def test_disabled_reader_is_refused_even_with_matching_profile():
    values = fixture()
    values["binding"]["enabled"] = False
    assert review_usb_variant(**values).reason == "reader_scope_unverified"


@pytest.mark.parametrize("binding_fingerprint,profile_fingerprint,expected", [
    ("uuid-alice-0001", "uuid-alice-0001", "review"),
    ("uuid-alice-0001", "uuid-alice-0002", "blocked"),
    ("uuid-alice-0002", "uuid-alice-0001", "blocked"),
    ("missing", "uuid-alice-0001", "blocked"),
    ("", "uuid-alice-0001", "blocked"),
    ("bad fingerprint !", "uuid-alice-0001", "blocked"),
    ("uuid-alice-0001", None, "blocked"),
])
def test_format_profile_is_bound_to_exact_usb_volume(
    binding_fingerprint, profile_fingerprint, expected,
):
    values = fixture()
    values["binding"]["metadata"]["usb_fingerprint"] = binding_fingerprint
    values["reader"] = UsbReaderFormatDeclaration(
        "alice", "reader-1", profile_fingerprint, ("epub",),
    )
    result = review_usb_variant(**values)
    assert result.status == expected
    assert result.executable is False


def test_missing_usb_fingerprint_is_not_a_reader_profile():
    values = fixture()
    values["binding"]["metadata"] = {}
    assert review_usb_variant(**values).reason == "reader_scope_unverified"
    values["binding"]["metadata"] = None
    assert review_usb_variant(**values).reason == "reader_scope_unverified"
