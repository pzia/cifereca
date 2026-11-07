"""One-copy preparation with real local bytes and canonical SQLite provenance."""
from dataclasses import replace

import pytest

from cifereca.core import device_usb_copy_preparation as preparation
from cifereca.core import device_usb_dry_run as dry_run
from cifereca.core.device_usb_copy_admission import UsbCopyConsent
from cifereca.core.device_usb_preview import UsbObservedFile
from tests.test_p1_2854_usb_copy_provenance import NOW, setup
from tests.test_p1_2854_usb_dry_run_coordinator import epub_bytes, fake_scan


@pytest.fixture
def copy_review(tmp_path, monkeypatch):
    payload = epub_bytes()
    db, rights = setup(tmp_path, payload=payload)
    device = {"identity_id": "alice", "device_id": "reader-a"}
    target = f"Books/Cifereca/{rights.sha256}.epub"
    root = tmp_path / "alice"
    mount = tmp_path / "synthetic-usb"
    mount.mkdir()
    db.device_usb_binding_declare(
        **device, usb_fingerprint=rights.usb_fingerprint,
        label="Reader A", enabled=True,
    )
    db.device_usb_reader_formats_set(
        **device, usb_fingerprint=rights.usb_fingerprint, supported_formats=("epub",),
    )
    db.device_usb_intent_set(
        **device, artifact_id=rights.artifact_id, desired_state="keep",
    )
    db.device_usb_target_set(
        **device, artifact_id=rights.artifact_id, target_relative_path=target,
    )
    consent = UsbCopyConsent(
        **device, usb_fingerprint=rights.usb_fingerprint,
        artifact_id=rights.artifact_id, sha256=rights.sha256,
        selected_format="epub", target_relative_path=target,
        operation_id="operation-test", confirmed_at=NOW - 2, expires_at=NOW + 20,
    )
    monkeypatch.setattr(dry_run, "scan_managed_usb", lambda **_: fake_scan())
    monkeypatch.setattr(
        dry_run, "preflight_usb_target", lambda **_: dry_run.UsbTargetPreflight(
            "review", "target_absent_space_observed", 1024**4,
        ),
    )
    return dict(
        storage=db, **device, artifact_id=rights.artifact_id,
        identity_root=root, mountpoint=mount, consent=consent, rights=rights,
        policy_verifier=lambda _: True, now=NOW,
    )


def test_preparation_combines_real_source_and_policy_without_mutation(copy_review):
    db = copy_review["storage"]
    before = db.device_placements()
    checked = []

    def policy(right):
        checked.append(right)
        return True

    review = preparation.prepare_usb_copy(**(copy_review | {"policy_verifier": policy}))
    assert review.status == "review"
    assert review.executable is False
    assert checked == [copy_review["rights"]]
    assert db.device_placements() == before
    assert list(copy_review["mountpoint"].iterdir()) == []


@pytest.mark.parametrize("key,reason", [
    ("consent", "copy_confirmation_required"),
    ("rights", "copy_rights_unverified"),
    ("policy_verifier", "copy_live_rights_resolver_unavailable"),
])
def test_missing_authority_never_starts_usb_io(copy_review, monkeypatch, key, reason):
    monkeypatch.setattr(
        dry_run, "scan_managed_usb", lambda **_: pytest.fail("unexpected USB scan"),
    )
    review = preparation.prepare_usb_copy(**(copy_review | {key: None}))
    assert (review.status, review.reason, review.executable) == ("blocked", reason, False)


def test_relocated_book_blocks_preparation_before_policy(copy_review, monkeypatch):
    seen = UsbObservedFile(
        "Books/Cifereca/renamed.epub", copy_review["rights"].sha256, regular_file=True,
    )
    monkeypatch.setattr(
        dry_run, "scan_managed_usb", lambda **_: fake_scan(files={"untracked:book": seen}),
    )
    copy_review["policy_verifier"] = lambda _: pytest.fail("unexpected policy verification")
    review = preparation.prepare_usb_copy(**copy_review)
    assert review.status == "blocked"
    assert review.reason == "observed_target_path_mismatch"


def test_other_account_cannot_prepare_copy(copy_review):
    copy_review["rights"] = replace(copy_review["rights"], account_id="other-account")
    copy_review["policy_verifier"] = lambda _: pytest.fail("unexpected policy verification")
    review = preparation.prepare_usb_copy(**copy_review)
    assert review.status == "blocked"
    assert review.reason == "copy_live_rights_not_attested"


def test_keep_intent_revoked_during_policy_blocks_preparation(copy_review):
    def policy(_right):
        copy_review["storage"].device_usb_intent_set(
            identity_id=copy_review["identity_id"], device_id=copy_review["device_id"],
            artifact_id=copy_review["artifact_id"], desired_state="remove",
        )
        return True

    review = preparation.prepare_usb_copy(**(copy_review | {"policy_verifier": policy}))
    assert review.status == "blocked"
    assert review.reason == "copy_scope_changed"


def test_selected_book_reuses_full_usb_inventory_but_only_its_preflight(
    copy_review, monkeypatch,
):
    db = copy_review["storage"]
    other = "sha256:" + "f" * 64
    db.artifact_put({
        "artifact_id": other, "sha256": "f" * 64, "size": 10,
        "media_format": "epub", "metadata": {"artifact_kind": "file"},
    })
    db.placement_put({
        "artifact_id": other, "path": str(copy_review["identity_root"] / "other.epub"),
        "identity_id": "alice", "provider_id": "local", "state": "present",
    })
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-a", artifact_id=other,
        desired_state="keep",
    )
    scanned, preflights = [], []

    def scan(**kwargs):
        scanned.extend(row["artifact_id"] for row in kwargs["placements"])
        return fake_scan()

    def preflight(**kwargs):
        preflights.append(kwargs["intention"]["artifact_id"])
        return dry_run.UsbTargetPreflight("review", "target_absent_space_observed", 1024**4)

    monkeypatch.setattr(dry_run, "scan_managed_usb", scan)
    monkeypatch.setattr(dry_run, "preflight_usb_target", preflight)
    result = preparation.prepare_usb_copy(**copy_review)
    assert result.status == "review"
    assert set(scanned) == {copy_review["artifact_id"], other}
    assert preflights == [copy_review["artifact_id"]]
