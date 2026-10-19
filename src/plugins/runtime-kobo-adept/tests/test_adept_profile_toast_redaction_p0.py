"""P0 #979: no vendor error text reaches Adobe profile page GTK notifications.

Pure offline projection tests; no GTK session, Adobe account, profile, device,
subprocess, ACSM, filesystem mutation or network call.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.plugin_api import AttentionRequired
from cifereca_kobo_adept_profiles import (
    _PROFILE_ERROR_FALLBACK, _PROFILE_ERROR_TOASTS, _safe_profile_error_toast,
)

SECRET = (
    "alice@example.invalid",
    "PRIVATE_CERTIFICATE_BLOB",
    "https://adobe.invalid/fulfillment?token=PRIVATE_SESSION",
    "<fulfillmentToken>PRIVATE_LICENSE</fulfillmentToken>",
    "/home/private-reader/.adobe-digital-editions/activation.xml",
)


class UnprintableError(Exception):
    def __str__(self):
        raise AssertionError("UI must never call str() on a vendor error")


class MaliciousCodeError(Exception):
    @property
    def code(self):
        raise RuntimeError("PRIVATE_SESSION from vendor property")


@pytest.mark.parametrize("code", sorted(_PROFILE_ERROR_TOASTS))
def test_attested_code_projects_fixed_public_label_without_vendor_message(code):
    exc = AttentionRequired(
        " ".join(SECRET), code=code,
    )
    label = _safe_profile_error_toast(exc)
    assert label == _PROFILE_ERROR_TOASTS[code]
    assert len(label) <= 240
    assert all(secret not in label for secret in SECRET)


@pytest.mark.parametrize("error", [
    RuntimeError(" ".join(SECRET)),
    ValueError(" ".join(SECRET)),
    UnprintableError(),
    MaliciousCodeError(),
    AttentionRequired(" ".join(SECRET), code="UNQUALIFIED_PRIVATE_SESSION"),
    AttentionRequired(" ".join(SECRET), code="E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER"),
])
def test_unknown_error_is_constant_and_not_stringified(error):
    label = _safe_profile_error_toast(error)
    assert label == _PROFILE_ERROR_FALLBACK
    assert all(secret not in label for secret in SECRET)


@pytest.mark.parametrize("code", [None, 1, True, [], {"token": "PRIVATE_SESSION"}])
def test_untyped_code_is_never_published(code):
    error = RuntimeError(" ".join(SECRET))
    error.code = code
    assert _safe_profile_error_toast(error) == _PROFILE_ERROR_FALLBACK


def test_profile_page_success_and_failure_toasts_use_closed_projection():
    path = Path(__file__).resolve().parents[1] / "cifereca_kobo_adept_profiles.py"
    source = path.read_text(encoding="utf-8")
    assert "host.toast(_safe_profile_error_toast(error))" in source
    assert "host.toast(str(error)" not in source
    assert "if success_message: host.toast(success_message)" in source
