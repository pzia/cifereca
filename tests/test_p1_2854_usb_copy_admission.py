"""P1 #2854: a copy admission requires two distinct exact live authorities."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from cifereca.core.device_usb_copy_admission import (
    UsbCopyAdmission,
    UsbCopyConsent,
    UsbCopyRightsEvidence,
    review_usb_copy_admission,
)
from cifereca.core.device_usb_preview import UsbLocalArtifact
from cifereca.core.device_usb_variant_review import UsbReaderFormatDeclaration


SHA = "a" * 64
ARTIFACT = "sha256:" + SHA
UUID = "uuid-alice-0001"
TARGET = "Books/Cifereca/" + SHA + ".epub"
NOW = 1800000120


def fixture():
    return dict(
        identity_id="alice",
        binding={
            "identity_id": "alice", "device_id": "reader-a",
            "enabled": True, "target_directory": "Books/Cifereca",
            "metadata": {"usb_fingerprint": UUID},
        },
        artifact={
            "artifact_id": ARTIFACT, "sha256": SHA, "size": 120,
            "kind": "file", "media_format": "epub", "metadata": {},
        },
        local=UsbLocalArtifact("alice", SHA, verified=True),
        selected_format="epub",
        target_relative_path=TARGET,
        reader=UsbReaderFormatDeclaration("alice", "reader-a", UUID, ("epub",)),
        rights=UsbCopyRightsEvidence(
            identity_id="alice", device_id="reader-a", usb_fingerprint=UUID,
            artifact_id=ARTIFACT, sha256=SHA, selected_format="epub",
            source_reference_id="source:scoped-provider-claim",
            provider_id="example", account_id="exact-account",
            source_kind="purchase", policy_ref="verified-explicit-usb-terms",
            observed_at=NOW - 30, valid_until=NOW + 100,
            personal_usb_copy_permitted=True,
        ),
        consent=UsbCopyConsent(
            identity_id="alice", device_id="reader-a", usb_fingerprint=UUID,
            artifact_id=ARTIFACT, sha256=SHA, selected_format="epub",
            target_relative_path=TARGET, operation_id="confirm-1",
            confirmed_at=NOW - 4, expires_at=NOW + 45,
        ),
        live_rights_resolver=lambda right: right,
        now=NOW,
    )


def test_independent_right_and_confirmation_remain_advisory():
    values = fixture()
    before = deepcopy(values["binding"]), deepcopy(values["artifact"])
    result = review_usb_copy_admission(**values)
    assert result == UsbCopyAdmission(
        "review", "copy_rights_and_consent_attested_for_review_only",
        "alice", "reader-a", ARTIFACT, False,
    )
    assert result.executable is False
    assert (values["binding"], values["artifact"]) == before


@pytest.mark.parametrize("key,reason", [
    ("rights", "copy_rights_unverified"),
    ("consent", "copy_confirmation_required"),
    ("live_rights_resolver", "copy_live_rights_resolver_unavailable"),
])
def test_missing_any_independent_authority_denied(key, reason):
    values = fixture()
    values[key] = None
    report = review_usb_copy_admission(**values)
    assert report.status == "blocked"
    assert report.reason == reason
    assert not report.executable


@pytest.mark.parametrize("attribute,value", [
    ("identity_id", "bob"), ("device_id", "reader-b"),
    ("usb_fingerprint", "uuid-other-0001"),
    ("artifact_id", "sha256:" + "b" * 64),
    ("sha256", "b" * 64), ("selected_format", "pdf"),
    ("source_reference_id", ""), ("provider_id", ""),
    ("account_id", "  "), ("policy_ref", ""),
    ("source_kind", "notice"), ("personal_usb_copy_permitted", False),
    ("personal_usb_copy_permitted", 1),
    ("observed_at", NOW + 1), ("observed_at", NOW - 301),
    ("observed_at", True),
    ("valid_until", NOW - 1), ("valid_until", NOW + 301),
    ("valid_until", True),
    ("loan_expires_at", NOW - 1),
])
def test_right_witness_cannot_move_across_scope_or_expire(attribute, value):
    values = fixture()
    values["rights"] = replace(values["rights"], **{attribute: value})
    report = review_usb_copy_admission(**values)
    assert report.reason == "copy_rights_invalid_or_expired"
    assert report.status == "blocked"
    assert not report.executable


@pytest.mark.parametrize("attribute,value", [
    ("identity_id", "bob"), ("device_id", "reader-b"),
    ("usb_fingerprint", "uuid-other-0001"),
    ("artifact_id", "sha256:" + "b" * 64),
    ("sha256", "b" * 64), ("selected_format", "pdf"),
    ("target_relative_path", "Books/Cifereca/other.epub"),
    ("operation_id", ""), ("operation", "remove"),
    ("confirmed_at", NOW + 1), ("confirmed_at", NOW - 301),
    ("confirmed_at", False), ("expires_at", NOW - 1),
    ("expires_at", NOW + 302), ("expires_at", False),
])
def test_consent_is_exact_fresh_upload_only(attribute, value):
    values = fixture()
    values["consent"] = replace(values["consent"], **{attribute: value})
    report = review_usb_copy_admission(**values)
    assert report.reason == "copy_confirmation_invalid_or_expired"
    assert report.status == "blocked"
    assert not report.executable


def test_exact_empty_primary_account_id_is_allowed_but_still_scoped():
    values = fixture()
    values["rights"] = replace(values["rights"], account_id="")
    report = review_usb_copy_admission(**values)
    assert report.status == "review"
    assert report.executable is False
    values["live_rights_resolver"] = lambda right: replace(
        right, account_id="other",
    )
    assert review_usb_copy_admission(**values).reason == (
        "copy_live_rights_not_attested"
    )


def test_loan_requires_separate_active_expiry():
    values = fixture()
    values["rights"] = replace(values["rights"], source_kind="loan")
    assert review_usb_copy_admission(**values).reason == "copy_rights_invalid_or_expired"
    values["rights"] = replace(values["rights"], loan_expires_at=NOW + 1)
    assert review_usb_copy_admission(**values).status == "review"
    values["now"] = NOW + 2
    assert review_usb_copy_admission(**values).reason == "copy_rights_invalid_or_expired"


@pytest.mark.parametrize("current", [None, True, "yes", 42])
def test_resolver_must_return_exact_typed_same_right(current):
    values = fixture()
    values["live_rights_resolver"] = lambda right: current
    report = review_usb_copy_admission(**values)
    assert report.reason == "copy_live_rights_not_attested"
    assert report.executable is False


def test_resolver_must_be_current_not_just_another_source():
    values = fixture()
    values["live_rights_resolver"] = lambda right: replace(
        right, source_reference_id="source:other-account",
    )
    report = review_usb_copy_admission(**values)
    assert report.reason == "copy_live_rights_not_attested"


def test_resolver_revocation_or_failure_blocks_without_side_effects():
    values = fixture()
    def denied(_):
        raise OSError("offline provider")
    values["live_rights_resolver"] = denied
    assert review_usb_copy_admission(**values).reason == (
        "copy_live_rights_verification_failed"
    )
    values["live_rights_resolver"] = lambda _: None
    assert review_usb_copy_admission(**values).reason == (
        "copy_live_rights_not_attested"
    )


@pytest.mark.parametrize("key,value,reason", [
    ("identity_id", "bob", "copy_variant_unverified"),
    ("selected_format", "pdf", "copy_variant_unverified"),
    ("target_relative_path", "Books/Cifereca/other.epub",
     "copy_confirmation_invalid_or_expired"),
    ("local", UsbLocalArtifact("bob", SHA, verified=True),
     "copy_variant_unverified"),
    ("reader", UsbReaderFormatDeclaration(
        "alice", "reader-a", UUID, ("pdf",),
    ), "copy_variant_unverified"),
])
def test_compatible_reader_and_owner_sha_are_preconditions(key, value, reason):
    values = fixture()
    values[key] = value
    result = review_usb_copy_admission(**values)
    assert result.status == "blocked"
    assert result.reason == reason
    assert result.executable is False


@pytest.mark.parametrize("protection", [
    {"protected": True}, {"artifact_protected": True},
    {"drm_status": "encrypted"}, {"drm_scheme": "adept"},
    {"drm_scheme": "kdrm"}, {"drm_scheme": "lcp"},
])
def test_positive_terms_cannot_override_explicit_protection(protection):
    values = fixture()
    values["artifact"]["metadata"] = protection
    report = review_usb_copy_admission(**values)
    assert report.reason == "copy_variant_unverified"
    assert not report.executable


def test_inert_artifact_metadata_never_substitutes_rights():
    values = fixture()
    values["rights"] = None
    values["artifact"]["metadata"] = {
        "post_processed": True, "protected": False,
        "drm_status": "none", "owned": True,
        "copy_allowed": True, "purchase": True,
    }
    assert review_usb_copy_admission(**values).reason == "copy_rights_unverified"


def test_invalid_clock_is_denied_before_rights_resolver_called():
    values = fixture()
    values["now"] = True
    values["live_rights_resolver"] = lambda _: pytest.fail("never called")
    assert review_usb_copy_admission(**values).reason == "copy_clock_unverified"
