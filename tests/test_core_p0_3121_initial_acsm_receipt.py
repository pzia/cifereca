"""#3121: offline receipt for the first one-shot Paris PNB ACSM.

Never persist a signed URL; verify exact loan/borrower and bytes on every read.
"""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from cifereca import acsm_cache
from cifereca.models import Loan

TOKEN = (b'<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
         b'<format>application/epub+zip</format></fulfillmentToken>')


@pytest.fixture
def first_offer(tmp_path: Path):
    directory = tmp_path / "private"
    directory.mkdir()
    path = directory / "first.acsm"
    path.write_bytes(TOKEN)
    loan = Loan(
        id="right-1", provider="DILICOM", medium="digital",
        record_id="edition-1", title="Synthetic",
        source_provider="paris", source_credential="credential-A",
        source_account_id="primary", identity_id="identity-A",
        due_at="2099-01-01T00:00:00Z",
        ebook_url="https://example.invalid/first.acsm?secret=do-not-persist",
    )
    return directory, path, loan


def test_first_offer_survives_lost_signed_url_and_has_no_secrets(first_offer):
    directory, path, loan = first_offer
    assert acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary", now=100)
    manifest = (directory / acsm_cache.INITIAL_MANIFEST).read_text()
    assert "do-not-persist" not in manifest
    assert "https://" not in manifest
    assert "fulfillmentToken" not in manifest
    # A fresh inventory may not carry the same one-shot signed URL.
    later = replace(loan, ebook_url=None)
    assert acsm_cache.reuse_initial(
        directory, later, "identity-A", "primary", now=101) == path


@pytest.mark.parametrize("field,value", [
    ("id", "right-2"), ("record_id", "edition-2"),
    ("source_credential", "credential-B"),
    ("source_provider", "bnf-pret"),
    ("source_account_id", "another"),
    ("identity_id", "identity-B"), ("provider", "OTHER"),
    ("medium", "physical"), ("due_at", "2098-01-01"),
    ("state", "returned"),
])
def test_first_offer_scope_changes_are_not_reusable(first_offer, field, value):
    directory, path, loan = first_offer
    assert acsm_cache.remember_initial(path, directory, loan, "identity-A",
                                       "primary", now=100)
    other = replace(loan, **{field: value})
    assert acsm_cache.reuse_initial(directory, other, "identity-A",
                                    "primary", now=101) is None


def test_first_offer_expiration_and_integrity_and_cleanup(first_offer):
    directory, path, loan = first_offer
    assert acsm_cache.remember_initial(path, directory, loan, "identity-A",
                                       "primary", now=100)
    assert acsm_cache.reuse_initial(directory, loan, "identity-A",
                                    "primary", now=99) is None
    assert acsm_cache.reuse_initial(directory, loan, "identity-A",
                                    "primary", now=100 + acsm_cache.TTL) is None
    path.write_bytes(TOKEN + b"tampered")
    assert acsm_cache.reuse_initial(directory, loan, "identity-A",
                                    "primary", now=101) is None
    path.write_bytes(TOKEN)
    acsm_cache.forget_initial(directory / "other.acsm")
    assert (directory / acsm_cache.INITIAL_MANIFEST).exists()
    acsm_cache.forget_initial(path)
    # No pathname unlink: a concurrent actor could replace the receipt just
    # before deletion. Retained sidecar is TTL-bounded for *reuse*, not disk.
    assert (directory / acsm_cache.INITIAL_MANIFEST).exists()
    assert acsm_cache.reuse_initial(directory, loan, "identity-A",
                                    "primary", now=100 + acsm_cache.TTL) is None


def test_first_offer_symlinks_or_forged_manifest_rejected(first_offer, tmp_path):
    directory, path, loan = first_offer
    assert acsm_cache.remember_initial(path, directory, loan, "identity-A",
                                       "primary", now=100)
    outside = tmp_path / "outside.acsm"
    outside.write_bytes(TOKEN)
    path.unlink()
    path.symlink_to(outside)
    assert acsm_cache.reuse_initial(directory, loan, "identity-A",
                                    "primary", now=101) is None
    assert not acsm_cache.remember_initial(path, directory, loan,
                                            "identity-A", "primary", now=101)
    path.unlink()
    path.write_bytes(TOKEN)
    manifest = directory / acsm_cache.INITIAL_MANIFEST
    row = json.loads(manifest.read_text())
    row["file"] = "../outside.acsm"
    manifest.write_text(json.dumps(row))
    assert acsm_cache.reuse_initial(directory, loan, "identity-A",
                                    "primary", now=101) is None
    manifest.unlink()
    manifest.symlink_to(outside)
    assert acsm_cache.reuse_initial(directory, loan, "identity-A",
                                    "primary", now=101) is None


def test_first_offer_is_never_remembered_without_exact_paris_scope(first_offer):
    directory, path, loan = first_offer
    assert not acsm_cache.remember_initial(path, directory, loan,
                                            "identity-B", "primary")
    assert not acsm_cache.remember_initial(path, directory, loan,
                                            "identity-A", "other")
    assert not (directory / acsm_cache.INITIAL_MANIFEST).exists()


def test_initial_manifest_rejects_oversize_and_non_regular_file(first_offer):
    directory, path, loan = first_offer
    assert acsm_cache.remember_initial(path, directory, loan, "identity-A",
                                       "primary", now=100)
    manifest = directory / acsm_cache.INITIAL_MANIFEST
    manifest.write_bytes(b" " * 16385)
    assert acsm_cache.reuse_initial(directory, loan, "identity-A",
                                    "primary", now=101) is None
    manifest.unlink()
    manifest.mkdir()
    assert acsm_cache.reuse_initial(directory, loan, "identity-A",
                                    "primary", now=101) is None


def test_initial_manifest_swapped_to_symlink_at_open_is_rejected(
        first_offer, tmp_path, monkeypatch):
    """A pathname race must not redirect the receipt read outside its dir."""
    import os

    directory, path, loan = first_offer
    assert acsm_cache.remember_initial(path, directory, loan, "identity-A",
                                       "primary", now=100)
    manifest = directory / acsm_cache.INITIAL_MANIFEST
    outside = tmp_path / "external.json"
    outside.write_bytes(manifest.read_bytes())
    original_open = os.open
    switched = False

    def swap_at_open(target, flags, *args, **kwargs):
        nonlocal switched
        if target == acsm_cache.INITIAL_MANIFEST and not switched:
            switched = True
            manifest.unlink()
            manifest.symlink_to(outside)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_at_open)
    assert acsm_cache.reuse_initial(directory, loan, "identity-A",
                                    "primary", now=101) is None
    assert switched


def test_forget_initial_does_not_unlink_replaced_sidecar(
        first_offer, tmp_path, monkeypatch):
    """Neither original nor concurrently substituted receipt is deleted."""
    import os

    directory, path, loan = first_offer
    assert acsm_cache.remember_initial(path, directory, loan, "identity-A",
                                       "primary", now=100)
    manifest = directory / acsm_cache.INITIAL_MANIFEST
    outside = tmp_path / "external.json"
    outside.write_text("unrelated", encoding="utf-8")
    manifest.unlink()
    manifest.symlink_to(outside)
    observed = []
    original_unlink = os.unlink

    def tracked_unlink(*args, **kwargs):
        observed.append(args)
        return original_unlink(*args, **kwargs)

    monkeypatch.setattr(os, "unlink", tracked_unlink)
    acsm_cache.forget_initial(path)
    assert observed == []
    assert manifest.is_symlink()
    assert outside.read_text(encoding="utf-8") == "unrelated"


def test_initial_receipt_refuses_revoked_scope_before_replace(
        first_offer, monkeypatch):
    """Revocation after hashing cannot publish a new valid receipt."""
    import os

    directory, path, loan = first_offer
    original_replace = os.replace
    admission = {"ok": True, "checks": 0}
    replaced = []

    def current():
        admission["checks"] += 1
        # Admission is revoked only after the initial verification and hash,
        # at the last gate immediately before publishing the sidecar.
        return admission["checks"] < 3

    def watch_replace(*args, **kwargs):
        replaced.append(args)
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(os, "replace", watch_replace)
    assert not acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary",
        now=100, admission_current=current)
    assert admission["checks"] >= 3
    assert replaced == []
    assert not (directory / acsm_cache.INITIAL_MANIFEST).exists()


def test_initial_receipt_refuses_revoked_scope_at_admission(first_offer):
    directory, path, loan = first_offer
    assert not acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary", now=100,
        admission_current=lambda: False)
    assert not (directory / acsm_cache.INITIAL_MANIFEST).exists()
