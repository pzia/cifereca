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
    # Receipt remains private provenance, while TTL still denies reuse.
    assert (directory / acsm_cache.INITIAL_MANIFEST).exists()
    assert acsm_cache.reuse_initial(
        directory, loan, "identity-A", "primary",
        now=100 + acsm_cache.TTL) is None


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


def test_claimed_initial_receipt_rechecks_confirmed_operation(first_offer):
    """A private file with valid bytes is not proof of an active borrow."""
    directory, path, loan = first_offer
    key = "loan.borrow.v1:" + "a" * 64
    operation = "operation-1"
    state = {
        "schema": 1, "operation": "loan.borrow",
        "status": "confirmed", "operation_id": operation,
        "request_accepted": True, "verification": "positive_active_loan",
        "verified_loan_id": loan.id,
    }
    assert acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary", now=100,
        borrow_claim=(key, operation))
    recorded = json.loads(
        (directory / acsm_cache.INITIAL_MANIFEST).read_text())
    assert recorded["borrow_claim"] == [key, operation]
    assert "https://" not in json.dumps(recorded)
    verify = lambda candidate: state if candidate == key else None
    assert acsm_cache.reuse_initial(
        directory, replace(loan, ebook_url=None), "identity-A", "primary",
        now=101, claim_state=verify) == path

    # Malformed, missing or foreign checkpoints do not authorize reuse.
    for changed in (
        None,
        {**state, "status": "verification_unavailable"},
        {**state, "operation_id": "another-operation"},
        {**state, "verified_loan_id": "another-right"},
        {**state, "request_accepted": False},
        {**state, "verification": "not_checked"},
        {**state, "schema": 2},
    ):
        assert acsm_cache.reuse_initial(
            directory, loan, "identity-A", "primary",
            now=101, claim_state=lambda _key, row=changed: row) is None


def test_legacy_initial_receipt_not_adopted_as_confirmed_claim(first_offer):
    directory, path, loan = first_offer
    # Historical receipts had no durable claim identity. Their bytes may
    # exist but cannot be promoted by Core under the new strict reader.
    assert acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary", now=100)
    assert acsm_cache.reuse_initial(
        directory, loan, "identity-A", "primary",
        now=101, claim_state=lambda _key: {
            "schema": 1, "status": "confirmed"}) is None


@pytest.mark.parametrize("claim", [
    ("loan.borrow.v1:" + "x" * 64, "operation-1"),
    ("loan.borrow.v1:" + "a" * 64, ""),
    ("loan.borrow.v1:" + "a" * 64, "x" * 129),
    ("loan.borrow.v1:" + "a" * 64, "invalid operation"),
    ("not-a-checkpoint", "operation-1"),
])
def test_invalid_claim_cannot_create_initial_receipt(first_offer, claim):
    directory, path, loan = first_offer
    assert not acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary",
        now=100, borrow_claim=claim)
    assert not (directory / acsm_cache.INITIAL_MANIFEST).exists()


def test_posthook_cache_preserves_initial_claim_binding(first_offer):
    """Postprocessing must not turn a conditional receipt into a free pass."""
    directory, path, loan = first_offer
    claim_key = "loan.borrow.v1:" + "b" * 64
    operation_id = "operation-posthook"
    accepted = {
        "schema": 1, "operation": "loan.borrow",
        "status": "confirmed", "operation_id": operation_id,
        "request_accepted": True, "verification": "positive_active_loan",
        "verified_loan_id": loan.id,
    }
    state = {claim_key: accepted}
    fetch = state.get
    assert acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary", now=100,
        borrow_claim=(claim_key, operation_id))
    assert acsm_cache.remember(
        path, directory, loan, "identity-A", "primary", now=101,
        initial_claim_bound=True, claim_state=fetch)
    later = replace(loan, ebook_url=None)
    # The normal cache is still URL keyed: with the fresh URL omitted it
    # cannot match. A same-URL read nevertheless has to check the claim.
    assert acsm_cache.reuse(
        directory, loan, "identity-A", "primary",
        now=102, claim_state=fetch) == path
    assert acsm_cache.reuse(
        directory, loan, "identity-A", "primary", now=102) is None
    assert acsm_cache.reuse_initial(
        directory, later, "identity-A", "primary",
        now=102, claim_state=fetch) == path
    state.clear()
    assert acsm_cache.reuse(
        directory, loan, "identity-A", "primary",
        now=103, claim_state=fetch) is None
    assert acsm_cache.reuse_initial(
        directory, later, "identity-A", "primary",
        now=103, claim_state=fetch) is None


def test_posthook_promotion_needs_live_claim_and_cannot_drop_binding(first_offer):
    directory, path, loan = first_offer
    claim_key = "loan.borrow.v1:" + "c" * 64
    assert acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary", now=100,
        borrow_claim=(claim_key, "op-A"))
    assert not acsm_cache.remember(
        path, directory, loan, "identity-A", "primary", now=101,
        initial_claim_bound=True, claim_state=lambda _key: None)
    assert not (directory / acsm_cache.MANIFEST).exists()
    # A retained initial receipt must never be silently downgraded into
    # an unrelated, unmarked ordinary cache for the SAME filename.
    acsm_cache.forget_initial(path)
    assert (directory / acsm_cache.INITIAL_MANIFEST).exists()
    assert acsm_cache.remember(
        path, directory, loan, "identity-A", "primary", now=102)
    assert acsm_cache.reuse(
        directory, loan, "identity-A", "primary", now=103) is None
    # The existing ordinary cache remains supported in an independent
    # work directory with no first-offer receipt.
    ordinary = directory.parent / "ordinary"
    ordinary.mkdir()
    independent = ordinary / path.name
    independent.write_bytes(TOKEN)
    assert acsm_cache.remember(
        independent, ordinary, loan, "identity-A", "primary", now=102)
    assert acsm_cache.reuse(
        ordinary, loan, "identity-A", "primary", now=103) == independent


def test_legacy_unmarked_posthook_cache_cannot_bypass_existing_initial_receipt(first_offer):
    """#4648-created sidecars remain protected after an in-place upgrade."""
    directory, path, loan = first_offer
    claim_key = "loan.borrow.v1:" + "d" * 64
    operation_id = "legacy-upgrade"
    assert acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary", now=100,
        borrow_claim=(claim_key, operation_id))
    # An older version of Core promoted initial bytes into an ordinary
    # cache sidecar without preserving the initial origin marker.
    assert acsm_cache.remember(
        path, directory, loan, "identity-A", "primary", now=101)
    assert acsm_cache.reuse(
        directory, loan, "identity-A", "primary",
        now=102, claim_state=lambda _key: None) is None
    confirmed = {
        "schema": 1, "operation": "loan.borrow",
        "status": "confirmed", "operation_id": operation_id,
        "request_accepted": True, "verification": "positive_active_loan",
        "verified_loan_id": loan.id,
    }
    assert acsm_cache.reuse(
        directory, loan, "identity-A", "primary",
        now=102, claim_state=lambda key: confirmed if key == claim_key else None) == path


def test_initial_receipt_rejects_revoked_lease_before_atomic_publication(first_offer, monkeypatch):
    """Epoch/claim rechecked after fsync and before publish, without a URL."""
    import os
    directory, path, loan = first_offer
    claim = ("loan.borrow.v1:" + "a" * 64, "operation-1")
    seen = {"checks": 0, "published": False}

    def admission():
        seen["checks"] += 1
        return seen["checks"] < 3

    original = os.replace

    def track_replace(*args, **kwargs):
        seen["published"] = True
        return original(*args, **kwargs)

    monkeypatch.setattr(os, "replace", track_replace)
    assert not acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary", now=100,
        borrow_claim=claim, admission_current=admission,
    )
    assert seen["checks"] >= 3
    assert seen["published"] is False
    assert not (directory / acsm_cache.INITIAL_MANIFEST).exists()


def test_initial_receipt_rejects_revoked_lease_at_entry(first_offer):
    directory, path, loan = first_offer
    assert not acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary",
        borrow_claim=("loan.borrow.v1:" + "b" * 64, "operation-1"),
        admission_current=lambda: False,
    )
    assert not (directory / acsm_cache.INITIAL_MANIFEST).exists()


def test_claimed_initial_receipt_never_unlinks_replaced_symlink(
        first_offer, tmp_path, monkeypatch):
    """A raced receipt pathname must not delete an unrelated file."""
    import os

    directory, path, loan = first_offer
    claim = ("loan.borrow.v1:" + "a" * 64, "operation-1")
    assert acsm_cache.remember_initial(
        path, directory, loan, "identity-A", "primary",
        borrow_claim=claim, now=100)
    manifest = directory / acsm_cache.INITIAL_MANIFEST
    other = tmp_path / "foreign.json"
    other.write_text("unrelated", encoding="utf-8")
    manifest.unlink()
    manifest.symlink_to(other)
    original = os.unlink
    unlinks = []

    def watched_unlink(*args, **kwargs):
        unlinks.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(os, "unlink", watched_unlink)
    acsm_cache.forget_initial(path)
    assert not unlinks
    assert manifest.is_symlink()
    assert other.read_text(encoding="utf-8") == "unrelated"
