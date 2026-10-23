"""P0 #2055: synthetic local BnF .bin gate, never a real user's license."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from dataclasses import asdict

import pytest

from cifereca.core.bnf_pret_saved_token import (
    MAX_SAVED_TOKEN_BYTES, SavedTokenEvidence, inspect_saved_bnf_token,
)


FIXED_NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def _token(*, fmt="application/epub+zip",
           expiration="2026-10-24T09:00:00+00:00",
           permission_until="2026-11-24T09:00:00+00:00",
           prefix=False):
    ns = "http://ns.adobe.com/adept"
    dc = "http://purl.org/dc/elements/1.1/"
    if prefix:
        head = f'<a:fulfillmentToken xmlns:a="{ns}" xmlns:dc="{dc}" fulfillmentType="loan" auth="user">'
        a = "a:"
    else:
        head = f'<fulfillmentToken xmlns="{ns}" xmlns:dc="{dc}" fulfillmentType="loan" auth="user">'
        a = ""
    return (
        head
        + f"<{a}distributor>synthetic-fake-uuid</{a}distributor>"
        + f"<{a}expiration>{expiration}</{a}expiration>"
        + f"<{a}resourceItemInfo><{a}metadata><dc:identifier></dc:identifier>"
        + f"<dc:format>{fmt}</dc:format></{a}metadata>"
        + f"<{a}licenseToken><{a}permissions><{a}display>"
        + f"<{a}until>{permission_until}</{a}until>"
        + f"</{a}display></{a}permissions></{a}licenseToken></{a}resourceItemInfo>"
        + f"<{a}hmac>FICTIONAL_PRIVATE_SIGNATURE</{a}hmac>"
        + (f"</a:fulfillmentToken>" if prefix else "</fulfillmentToken>")
    ).encode("utf-8")


@pytest.mark.parametrize("prefix", [True, False])
@pytest.mark.parametrize("fmt,expected", [
    ("application/pdf", "pdf"),
    ("application/epub+zip", "epub"),
    ("unknown", None),
])
def test_bin_with_real_adept_shape_is_only_candidate(tmp_path, prefix, fmt, expected):
    path = tmp_path / "cifereca-telechargement-01.bin"
    path.write_bytes(_token(fmt=fmt, prefix=prefix))
    result = inspect_saved_bnf_token(path, now=FIXED_NOW)
    assert result == SavedTokenEvidence("candidate_acsm", expected, True)
    assert "FICTIONAL_PRIVATE_SIGNATURE" not in repr(result)
    assert str(path) not in repr(result)
    assert "synthetic-fake-uuid" not in json.dumps(asdict(result))


def test_token_expiry_is_not_drm_permission_until_or_library_loan_due_date(tmp_path):
    path = tmp_path / "private.bin"
    path.write_bytes(_token(
        expiration="2026-10-22T22:30:00+02:00",
        permission_until="2026-12-20T00:00:00+00:00",
    ))
    result = inspect_saved_bnf_token(path, now=FIXED_NOW)
    assert result == SavedTokenEvidence("token_expired", "epub", True)
    assert "2026" not in repr(result)  # no private expiry in diagnostics


@pytest.mark.parametrize("body,code", [
    (b"<html><body>login</body></html>", "not_acsm"),
    (b"%PDF-1.4\n", "invalid_xml"),
    (b"PK\x03\x04", "invalid_xml"),
    (b"<fulfillmentToken", "invalid_xml"),
    (b"<!DOCTYPE foo [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><foo>&x;</foo>", "unsafe_xml"),
    (b"<!DoCtYpE foo><foo/>", "unsafe_xml"),
    (b"\xff\xfe<\x00f\x00", "invalid_xml"),
    (b"", "invalid_xml"),
])
def test_non_acsm_or_hostile_bin_does_not_gain_rights(tmp_path, body, code):
    path = tmp_path / "private.bin"
    path.write_bytes(body)
    assert inspect_saved_bnf_token(path, now=FIXED_NOW).code == code


def test_bounded_read_and_missing_file_do_not_leak_path(tmp_path):
    path = tmp_path / "person-name.bin"
    assert inspect_saved_bnf_token(path, now=FIXED_NOW).code == "file_unavailable"
    path.write_bytes(b"X" * (MAX_SAVED_TOKEN_BYTES + 1))
    assert inspect_saved_bnf_token(path, now=FIXED_NOW).code == "token_too_large"
    assert "person-name" not in repr(inspect_saved_bnf_token(path, now=FIXED_NOW))


def test_symlink_and_hardlink_are_refused(tmp_path):
    original = tmp_path / "original.bin"
    original.write_bytes(_token())
    alias = tmp_path / "alias.bin"
    alias.symlink_to(original)
    assert inspect_saved_bnf_token(alias, now=FIXED_NOW).code == "file_unavailable"
    alias.unlink()
    os.link(original, alias)
    assert inspect_saved_bnf_token(alias, now=FIXED_NOW).code == "file_unavailable"


def test_missing_expiry_yields_unknown_not_claim_of_unexpired_loan(tmp_path):
    path = tmp_path / "one.bin"
    path.write_bytes(_token().replace(
        b"<expiration>2026-10-24T09:00:00+00:00</expiration>",
        b"",
    ))
    result = inspect_saved_bnf_token(path, now=FIXED_NOW)
    assert result == SavedTokenEvidence("candidate_acsm", "epub", False)


@pytest.mark.parametrize("expiration", [
    "2026-10-24T09:00:00",
    "not-a-date",
    "2030-" + "1" * 80,
])
def test_unverified_clock_cannot_be_misreported_as_valid(tmp_path, expiration):
    path = tmp_path / "one.bin"
    path.write_bytes(_token(expiration=expiration))
    assert inspect_saved_bnf_token(path, now=FIXED_NOW).code == "invalid_expiry"


def test_read_only_does_not_copy_convert_or_invoke_adept(tmp_path):
    path = tmp_path / "one.bin"
    raw = _token()
    path.write_bytes(raw)
    before = path.stat().st_mtime_ns
    result = inspect_saved_bnf_token(path, now=FIXED_NOW)
    assert result.media_format == "epub"
    assert path.read_bytes() == raw
    assert path.stat().st_mtime_ns == before
    assert list(tmp_path.iterdir()) == [path]
