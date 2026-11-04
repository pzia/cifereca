"""P1 #2854: canonical provenance and a live policy are independent gates."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest

from cifereca.core.device_usb_copy_admission import (
    UsbCopyConsent, UsbCopyRightsEvidence, review_usb_copy_admission,
)
from cifereca.core.device_usb_copy_provenance import resolve_live_usb_copy_right
from cifereca.core.device_usb_preview import UsbLocalArtifact
from cifereca.core.device_usb_variant_review import UsbReaderFormatDeclaration
from cifereca.identities import Identity
from cifereca.storage import Storage


NOW = 1800000120
PAYLOAD = b"synthetic test copy"
SHA = sha256(PAYLOAD).hexdigest()
ARTIFACT = "sha256:" + SHA
SOURCE = "source:alice-exact"
UUID = "uuid-alice-0001"
TARGET = "Books/Cifereca/" + SHA + ".epub"


def setup(tmp_path, *, owner="alice", kind="provider", account="account-one",
          source_kind="purchase", source_state="active", placement=True):
    db = Storage(tmp_path / "library.sqlite3")
    db.identity_put(Identity("alice", "Alice"))
    db.identity_put(Identity("bob", "Bob"))
    db.artifact_put({
        "artifact_id": ARTIFACT, "sha256": SHA, "size": len(PAYLOAD),
        "media_format": "epub", "metadata": {"artifact_kind": "file"},
    })
    root = tmp_path / owner
    root.mkdir()
    file = root / "book.epub"
    file.write_bytes(PAYLOAD)
    if placement:
        db.placement_put({
            "artifact_id": ARTIFACT, "path": str(file),
            "identity_id": owner, "provider_id": "provider-one",
            "source_item_id": "item-one", "state": "present",
            "metadata": {"artifact_kind": "file"},
        })
    with db._lock, db._connect() as con:
        con.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,state,"
            "synchronizable,evidence_kind,evidence_ref,created_at,updated_at)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                SOURCE, owner, "provider-one", "credential-one",
                account, "purchase-history", "edition-one", "right-one",
                source_kind, source_state, 1, "provider_response",
                "exact-response", NOW, NOW,
            ),
        )
        con.execute(
            "INSERT INTO origin_links("
            "origin_link_id,origin_kind,identity_id,artifact_id,"
            "source_reference_id,evidence_ref,created_at,updated_at)"
            "VALUES(?,?,?,?,?,?,?,?)",
            ("origin:alice-one", kind, owner, ARTIFACT, SOURCE,
             "verified-delivery", NOW, NOW),
        )
    witness = UsbCopyRightsEvidence(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint=UUID, artifact_id=ARTIFACT, sha256=SHA,
        selected_format="epub", source_reference_id=SOURCE,
        provider_id="provider-one", account_id=account,
        source_kind=source_kind, policy_ref="provider-terms-copy-grant",
        observed_at=NOW - 10, valid_until=NOW + 60,
        personal_usb_copy_permitted=True,
    )
    if source_kind == "loan":
        witness = replace(witness, loan_expires_at=NOW + 90)
    return db, witness


def check(db, witness, *, verifier=lambda _: True, now=NOW):
    return resolve_live_usb_copy_right(
        db, witness, policy_verifier=verifier, now=now,
    )


def test_canonical_source_and_independent_policy_both_required(tmp_path):
    db, witness = setup(tmp_path)
    calls = []
    def issuer(right):
        calls.append(right)
        return True
    assert check(db, witness, verifier=issuer) == witness
    assert calls == [witness]
    assert check(db, witness, verifier=None) is None
    assert check(db, witness, verifier=lambda _: False) is None


@pytest.mark.parametrize("scope", [
    {"identity_id": "bob"}, {"provider_id": "other-provider"},
    {"account_id": "account-two"}, {"source_reference_id": "source:other"},
    {"sha256": "f" * 64}, {"artifact_id": "sha256:" + "f" * 64},
    {"selected_format": "pdf"}, {"source_kind": "loan"},
])
def test_source_cannot_be_borrowed_between_accounts_and_artifacts(
    tmp_path, scope,
):
    db, witness = setup(tmp_path)
    witness = replace(witness, **scope)
    assert check(db, witness) is None


def test_manual_origin_cannot_mint_provider_usb_copy_rights(tmp_path):
    db, witness = setup(tmp_path, kind="manual")
    assert check(db, witness) is None


@pytest.mark.parametrize("kwargs", [
    {"owner": "bob"}, {"source_state": "inactive"},
    {"source_state": "unknown"}, {"placement": False},
])
def test_missing_active_same_owner_canonical_source_denied(tmp_path, kwargs):
    db, witness = setup(tmp_path, **kwargs)
    assert check(db, witness) is None


@pytest.mark.parametrize("attestation", [None, False, "yes", 1])
def test_provider_copy_policy_must_be_true_boolean(tmp_path, attestation):
    db, witness = setup(tmp_path)
    assert check(db, witness, verifier=lambda _: attestation) is None


def test_current_source_revocation_during_policy_probe_is_refused(tmp_path):
    db, witness = setup(tmp_path)
    def revoke(_right):
        with db._lock, db._connect() as con:
            con.execute(
                "UPDATE source_references SET state='inactive' "
                "WHERE source_reference_id=?", (SOURCE,),
            )
        return True
    assert check(db, witness, verifier=revoke) is None


def test_policy_exception_fails_closed(tmp_path):
    db, witness = setup(tmp_path)
    def fail(_right):
        raise RuntimeError("provider unavailable")
    assert check(db, witness, verifier=fail) is None


def test_exact_primary_empty_account_stays_valid(tmp_path):
    db, witness = setup(tmp_path, account="")
    assert check(db, witness) == witness
    assert check(db, replace(witness, account_id="elsewhere")) is None


def test_expired_loan_never_authorizes_canonical_source(tmp_path):
    db, witness = setup(tmp_path, source_kind="loan")
    assert check(db, witness) == witness
    assert check(db, replace(witness, loan_expires_at=NOW - 1)) is None
    assert check(db, replace(witness, valid_until=NOW - 1)) is None


def test_right_and_consent_review_accepts_only_live_canonical_issuer(
    tmp_path,
):
    db, witness = setup(tmp_path)
    binding = {
        "identity_id": "alice", "device_id": "reader-a",
        "enabled": True, "target_directory": "Books/Cifereca",
        "metadata": {"usb_fingerprint": UUID},
    }
    artifact = {
        "artifact_id": ARTIFACT, "sha256": SHA, "size": len(PAYLOAD),
        "kind": "file", "media_format": "epub", "metadata": {},
    }
    args = dict(
        identity_id="alice", binding=binding, artifact=artifact,
        local=UsbLocalArtifact("alice", SHA, True),
        selected_format="epub", target_relative_path=TARGET,
        reader=UsbReaderFormatDeclaration("alice", "reader-a", UUID, ("epub",)),
        rights=witness,
        consent=UsbCopyConsent(
            identity_id="alice", device_id="reader-a",
            usb_fingerprint=UUID, artifact_id=ARTIFACT,
            sha256=SHA, selected_format="epub",
            target_relative_path=TARGET, operation_id="manual-confirm-1",
            confirmed_at=NOW - 1, expires_at=NOW + 50,
        ),
        live_rights_resolver=lambda r: check(db, r),
        now=NOW,
    )
    admitted = review_usb_copy_admission(**args)
    assert admitted.status == "review"
    assert admitted.executable is False

    args["live_rights_resolver"] = lambda r: check(
        db, r, verifier=lambda _: False,
    )
    denied = review_usb_copy_admission(**args)
    assert denied.status == "blocked"
    assert denied.reason == "copy_live_rights_not_attested"
    assert denied.executable is False
