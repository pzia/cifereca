"""P1 #2854: a dry-run can observe but NEVER authorize USB side effects.

Integration with real SQLite and real local file SHA, USB scan mocked by a
synthetic complete observation. No hardware mounted or network contacted.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from cifereca.core import device_usb_dry_run as run
from cifereca.core.device_usb_preview import UsbDeviceScan, UsbObservedFile
from cifereca.identities import Identity
from cifereca.storage import Storage


# Fixture archives are generated during pytest collection by independent xdist
# workers. ZipFile.writestr(name, ...) embeds the current local time and can
# change the bytes, SHA and collection IDs across a two-second ZIP tick.
_EPUB_FIXTURE_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


def epub_bytes(*, encrypted=False) -> bytes:
    sink = BytesIO()
    with ZipFile(sink, "w", compression=ZIP_DEFLATED) as archive:
        def add(path: str, content: str) -> None:
            info = ZipInfo(path, date_time=_EPUB_FIXTURE_TIMESTAMP)
            # Each member has a deterministic header, compressor and order.
            archive.writestr(info, content, compress_type=ZIP_DEFLATED)

        add("mimetype", "application/epub+zip")
        add(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OEBPS/package.opf"/>'
            '</rootfiles></container>',
        )
        add("OEBPS/package.opf", "<package/>")
        if encrypted:
            add("META-INF/encryption.xml", "<EncryptedData>AES</EncryptedData>")
    return sink.getvalue()


PAYLOAD = epub_bytes()
DIGEST = sha256(PAYLOAD).hexdigest()
ARTIFACT = "sha256:" + DIGEST
TARGET = "Books/Cifereca/" + DIGEST + ".epub"
NOW = 1800000120


@pytest.mark.parametrize("encrypted", [False, True], ids=("plain", "encrypted"))
def test_epub_fixture_has_fixed_zip_headers_for_xdist_collection(encrypted):
    first, second = epub_bytes(encrypted=encrypted), epub_bytes(encrypted=encrypted)
    assert first == second
    assert first.startswith(b"PK")
    with ZipFile(BytesIO(first)) as archive:
        assert all(member.date_time == _EPUB_FIXTURE_TIMESTAMP
                   for member in archive.infolist())
        assert all(member.compress_type == ZIP_DEFLATED
                   for member in archive.infolist())
        assert ("META-INF/encryption.xml" in archive.namelist()) is encrypted
    if not encrypted:
        assert first == PAYLOAD
        assert sha256(first).hexdigest() == DIGEST
        assert TARGET.endswith(DIGEST + ".epub")


@pytest.fixture(autouse=True)
def synthetic_target_preflight(monkeypatch):
    # Synthetic scans do not represent real block-device mounts. Mock only
    # the separate physical preflight boundary; its own module is covered by
    # dedicated tests with real temporary filesystem descriptors.
    monkeypatch.setattr(
        run, "preflight_usb_target", lambda **_: run.UsbTargetPreflight(
            "review", "target_absent_space_observed", 1024**4,
        ),
    )


def setup(tmp_path: Path, *, enabled=True, desired="keep", target=True):
    db = Storage(tmp_path / "library.sqlite3")
    db.identity_put(Identity("alice", "Alice"))
    db.identity_put(Identity("bob", "Bob"))
    root = tmp_path / "alice"
    root.mkdir()
    book = root / "test.epub"
    book.write_bytes(PAYLOAD)
    db.artifact_put({
        "artifact_id": ARTIFACT, "sha256": DIGEST, "size": len(PAYLOAD),
        "media_format": "epub", "metadata": {"artifact_kind": "file"},
    })
    db.placement_put({
        "artifact_id": ARTIFACT, "path": str(book), "identity_id": "alice",
        "provider_id": "local", "source_item_id": "book-one",
        "state": "present", "metadata": {},
    })
    db.device_usb_binding_declare(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001",
        label="Reader A", enabled=enabled,
    )
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-a",
        artifact_id=ARTIFACT, desired_state="keep",
    )
    if target:
        db.device_usb_target_set(
            identity_id="alice", device_id="reader-a",
            artifact_id=ARTIFACT, target_relative_path=TARGET,
        )
    if desired == "remove":
        db.device_usb_intent_set(
            identity_id="alice", device_id="reader-a",
            artifact_id=ARTIFACT, desired_state="remove",
        )
    return db, root, book, tmp_path / "fake-mount"


def fake_scan(*, identity_id="alice", device_id="reader-a", files=None,
              fingerprint="uuid-alice-0001", observed_at=NOW):
    return UsbDeviceScan(
        identity_id=identity_id, device_id=device_id,
        fingerprint=fingerprint, observed_at=observed_at,
        complete=True, files={} if files is None else files,
    )


def inspect(db, root, mount):
    return run.inspect_usb_dry_run(
        storage=db, identity_id="alice", device_id="reader-a",
        identity_root=root, mountpoint=mount, now=NOW,
    )


def test_verified_owned_source_and_complete_scan_only_propose_upload(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    calls = []
    monkeypatch.setattr(
        run, "scan_managed_usb",
        lambda **kwargs: calls.append(kwargs) or fake_scan(),
    )
    before = deepcopy(db.device_placements())
    result = inspect(db, root, mount)
    assert result.status == "scanned"
    assert result.scan_attested is True
    assert result.observed_at == NOW
    assert result.executable is False
    assert len(result.rows) == 1
    assert (result.rows[0].action, result.rows[0].observed) == (
        "upload_candidate", "missing",
    )
    assert result.rows[0].executable is False
    assert result.rows[0].reason == "epub_drm_and_transfer_rights_unverified"
    assert calls[0]["binding"]["identity_id"] == "alice"
    assert calls[0]["mountpoint"] == mount
    assert db.device_placements() == before
    assert book.read_bytes() == PAYLOAD


@pytest.mark.parametrize("payload,expected", [
    (b"invalid EPUB ZIP file", "content_structure_unverified"),
    (epub_bytes(encrypted=True), "content_protection_detected"),
], ids=("invalid_zip", "encrypted_epub"))
def test_real_file_structure_and_encryption_block_after_sha(
    tmp_path, monkeypatch, payload, expected,
):
    db, root, book, mount = setup(tmp_path)
    db.device_placement_delete("reader-a", ARTIFACT)
    new_sha = sha256(payload).hexdigest()
    new_id = "sha256:" + new_sha
    new_file = root / "other.epub"
    new_file.write_bytes(payload)
    db.artifact_put({
        "artifact_id": new_id, "sha256": new_sha, "size": len(payload),
        "media_format": "epub", "metadata": {"artifact_kind": "file"},
    })
    db.placement_put({
        "artifact_id": new_id, "path": str(new_file), "identity_id": "alice",
        "provider_id": "local", "source_item_id": "other",
        "state": "present", "metadata": {},
    })
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-a",
        artifact_id=new_id, desired_state="keep",
    )
    db.device_usb_target_set(
        identity_id="alice", device_id="reader-a",
        artifact_id=new_id, target_relative_path="Books/Cifereca/" + new_sha + ".epub",
    )
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    result = inspect(db, root, mount)
    assert result.status == "scanned"
    row, = result.rows
    assert row.artifact_id == new_id
    assert row.action == "blocked"
    assert row.reason == expected
    assert row.executable is False
    assert new_file.read_bytes() == payload


def test_changed_local_content_between_sha_and_snapshot_is_not_qualified(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    original_snapshot = run.snapshot_local_usb_content

    def mutate_bytes(**kwargs):
        book.write_bytes(b"X" * len(PAYLOAD))
        return original_snapshot(**kwargs)

    monkeypatch.setattr(run, "snapshot_local_usb_content", mutate_bytes)
    result = inspect(db, root, mount)
    assert result.status == "scanned"
    assert result.rows[0].action == "verify"
    assert result.rows[0].reason == "content_bytes_unverified"
    assert result.rows[0].executable is False


def test_revoking_formats_during_content_read_discards_all_rows(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    original_snapshot = run.snapshot_local_usb_content

    def revoke(**kwargs):
        content = original_snapshot(**kwargs)
        db.device_usb_reader_formats_set(
            identity_id="alice", device_id="reader-a",
            usb_fingerprint="uuid-alice-0001", supported_formats=("pdf",),
        )
        return content

    monkeypatch.setattr(run, "snapshot_local_usb_content", revoke)
    result = inspect(db, root, mount)
    assert result.status == "scope_changed"
    assert result.rows == ()
    assert result.executable is False


def test_bounded_content_inspection_does_not_read_oversized_file(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    monkeypatch.setattr(run, "MAX_SNAPSHOT_BYTES", 1)
    monkeypatch.setattr(
        run, "snapshot_local_usb_content",
        lambda **_: pytest.fail("content must not be read beyond budget"),
    )
    result = inspect(db, root, mount)
    assert result.status == "scanned"
    assert result.rows[0].action == "verify"
    assert result.rows[0].reason == "content_inspection_size_limit"
    assert result.rows[0].executable is False


def test_no_reader_formats_declaration_keeps_upload_non_executable(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    before = deepcopy(db.device_placements())
    result = inspect(db, root, mount)
    assert result.status == "scanned"
    assert result.rows[0].action == "verify"
    assert result.rows[0].reason == "reader_formats_unconfigured"
    assert result.rows[0].observed == "missing"
    assert result.rows[0].executable is False
    assert db.device_placements() == before
    assert book.read_bytes() == PAYLOAD


def test_reader_explicitly_rejecting_epub_blocks_upload(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("pdf",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    result = inspect(db, root, mount)
    assert result.status == "scanned"
    assert result.rows[0].action == "blocked"
    assert result.rows[0].reason == "reader_format_unsupported"
    assert result.rows[0].observed == "missing"
    assert not result.rows[0].executable


def test_declared_formats_never_override_reported_protection(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    original = db.artifact_get(ARTIFACT)
    db.artifact_put({
        **original,
        "metadata": {**original["metadata"], "artifact_protected": True},
    })
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    result = inspect(db, root, mount)
    assert result.status == "scanned"
    assert result.rows[0].action == "blocked"
    assert result.rows[0].reason == "protection_reported"
    assert result.rows[0].executable is False


def test_format_declaration_changed_during_scan_invalidates_all_rows(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    def changing_scan(**_):
        db.device_usb_reader_formats_set(
            identity_id="alice", device_id="reader-a",
            usb_fingerprint="uuid-alice-0001", supported_formats=("pdf",),
        )
        return fake_scan()
    monkeypatch.setattr(run, "scan_managed_usb", changing_scan)
    result = inspect(db, root, mount)
    assert result.status == "scope_changed"
    assert result.rows == ()
    assert result.executable is False


def test_revoking_formats_returns_to_unconfigured_without_physical_change(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    assert inspect(db, root, mount).rows[0].action == "upload_candidate"
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=(),
    )
    report = inspect(db, root, mount)
    assert report.rows[0].reason == "reader_formats_unconfigured"
    assert report.rows[0].action == "verify"
    assert report.rows[0].executable is False


@pytest.mark.parametrize("preflight_reason,expected_action", [
    ("target_occupied", "blocked"),
    ("target_volume_read_only", "blocked"),
    ("target_space_insufficient", "blocked"),
    ("target_directory_unbounded", "verify"),
])
def test_physical_target_refusals_stop_content_read(
    tmp_path, monkeypatch, preflight_reason, expected_action,
):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    calls = []
    def fake_preflight(**kwargs):
        calls.append(kwargs)
        return run.UsbTargetPreflight("blocked", preflight_reason)
    monkeypatch.setattr(run, "preflight_usb_target", fake_preflight)
    monkeypatch.setattr(
        run, "snapshot_local_usb_content",
        lambda **_: pytest.fail("blocked USB target must stop content inspection"),
    )
    before = deepcopy(db.device_placements())
    result = inspect(db, root, mount)
    assert result.status == "scanned"
    assert result.rows[0].action == expected_action
    assert result.rows[0].reason == preflight_reason
    assert result.rows[0].executable is False
    assert calls[0]["identity_id"] == "alice"
    assert calls[0]["binding"]["device_id"] == "reader-a"
    assert calls[0]["intention"]["target_relative_path"] == TARGET
    assert db.device_placements() == before
    assert book.read_bytes() == PAYLOAD


@pytest.mark.parametrize("reason", [
    "target_mount_changed", "target_mount_unverified",
    "target_scope_unverified", "target_io_unverified",
    "target_directory_changed", "target_directory_unverified",
])
def test_lost_physical_mount_or_directory_attestation_discards_all_rows(
    tmp_path, monkeypatch, reason,
):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    monkeypatch.setattr(
        run, "preflight_usb_target",
        lambda **_: run.UsbTargetPreflight("blocked", reason),
    )
    result = inspect(db, root, mount)
    assert result.status == "scope_unverified"
    assert result.rows == ()
    assert result.scan_attested is False
    assert result.observed_at is None
    assert result.executable is False


def test_available_space_must_cover_this_batch_plus_reserve(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    monkeypatch.setattr(
        run, "preflight_usb_target",
        lambda **_: run.UsbTargetPreflight(
            "review", "target_absent_space_observed",
            len(PAYLOAD) + run._USB_FREE_SPACE_RESERVE - 1,
        ),
    )
    monkeypatch.setattr(
        run, "snapshot_local_usb_content",
        lambda **_: pytest.fail("insufficient space must stop content read"),
    )
    result = inspect(db, root, mount)
    assert result.rows[0].action == "blocked"
    assert result.rows[0].reason == "target_batch_capacity_insufficient"
    assert result.rows[0].executable is False


def test_preflight_budget_fails_closed_without_physical_access(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    monkeypatch.setattr(run, "_MAX_TARGET_PREFLIGHTS", 0)
    monkeypatch.setattr(
        run, "preflight_usb_target",
        lambda **_: pytest.fail("bounded dry run may not inspect target"),
    )
    result = inspect(db, root, mount)
    assert result.rows[0].action == "verify"
    assert result.rows[0].reason == "target_preflight_budget_exhausted"
    assert not result.rows[0].executable


def test_forged_positive_preflight_executable_discards_whole_report(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    monkeypatch.setattr(
        run, "preflight_usb_target",
        lambda **_: replace(run.UsbTargetPreflight(
            "review", "target_absent_space_observed", 1024**4,
        ), executable=True),
    )
    result = inspect(db, root, mount)
    assert result.status == "scope_unverified"
    assert result.rows == ()
    assert result.executable is False


def test_binding_revoked_during_target_preflight_discards_all_results(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    db.device_usb_reader_formats_set(
        identity_id="alice", device_id="reader-a",
        usb_fingerprint="uuid-alice-0001", supported_formats=("epub",),
    )
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    def rebind(**kwargs):
        current = db.device_binding_get("reader-a")
        db.device_binding_put({
            **current, "enabled": False,
        })
        return run.UsbTargetPreflight(
            "review", "target_absent_space_observed", 1024**4,
        )
    monkeypatch.setattr(run, "preflight_usb_target", rebind)
    result = inspect(db, root, mount)
    assert result.status == "scope_changed"
    assert result.rows == ()
    assert result.executable is False


def test_scanned_identical_bytes_are_skip_not_a_new_managed_receipt(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_:
        fake_scan(files={ARTIFACT: UsbObservedFile(
            relative_path=TARGET, sha256=DIGEST, regular_file=True,
        )}),
    )
    before = deepcopy(db.device_placements())
    result = inspect(db, root, mount)
    assert result.rows[0].action == "skip"
    assert result.rows[0].observed == "present"
    assert result.rows[0].executable is False
    assert db.device_placements() == before
    assert book.read_bytes() == PAYLOAD


def test_remove_wish_is_blocked_without_managed_write_receipt(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path, desired="remove")
    db.device_placement_put({
        "device_id": "reader-a", "artifact_id": ARTIFACT,
        "desired_state": "remove", "observed_state": "present",
        "target_relative_path": TARGET, "last_observed_at": 1700000000,
        "metadata": {"intent_identity_id": "alice"},
    })
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_:
        fake_scan(files={ARTIFACT: UsbObservedFile(
            relative_path=TARGET, sha256=DIGEST, regular_file=True,
        )}),
    )
    before = deepcopy(db.device_placements())
    result = inspect(db, root, mount)
    assert result.rows[0].action == "blocked"
    assert result.rows[0].reason == "unmanaged_or_changed_file"
    assert result.rows[0].observed == "present"
    assert result.rows[0].executable is False
    assert db.device_placements() == before
    assert book.read_bytes() == PAYLOAD


def test_inaccessible_volume_never_proves_missing_from_old_sqlite(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    db.device_placement_put({
        "device_id": "reader-a", "artifact_id": ARTIFACT,
        "desired_state": "keep", "observed_state": "present",
        "target_relative_path": TARGET, "last_observed_at": 1700000000,
        "metadata": {"intent_identity_id": "alice"},
    })
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: None)
    before = deepcopy(db.device_placements())
    result = inspect(db, root, mount)
    assert result.status == "device_unverified"
    assert result.scan_attested is False
    assert result.observed_at is None
    assert result.rows[0].action == "verify"
    assert result.rows[0].observed == "unknown"
    assert result.rows[0].last_known == "present"
    assert db.device_placements() == before


def test_missing_local_book_is_never_qualified_as_upload(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    book.unlink()
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    result = inspect(db, root, mount)
    assert result.status == "scanned"
    assert result.rows[0].action == "blocked"
    assert result.rows[0].reason == "owned_source_unverified"
    assert not result.rows[0].executable


def test_undeclared_target_must_be_qualified_before_positively_missing(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path, target=False)
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: fake_scan())
    result = inspect(db, root, mount)
    assert result.rows[0].observed == "unknown"
    assert result.rows[0].reason == "target_path_unqualified"
    assert result.rows[0].executable is False


def test_disabled_reader_cannot_invent_present_or_missing(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path, enabled=False)
    def forbid(**kwargs):
        raise AssertionError("disabled reader must not be scanned")
    monkeypatch.setattr(run, "scan_managed_usb", forbid)
    result = inspect(db, root, mount)
    assert result.status == "device_unverified"
    assert result.rows[0].action == "blocked"
    assert result.rows[0].observed == "unknown"


def test_unavailable_volume_never_hashes_local_epubs(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: None)
    monkeypatch.setattr(
        run, "attest_local_usb_source",
        lambda **_: pytest.fail("no local SHA work without complete USB scan"),
    )
    result = inspect(db, root, mount)
    assert result.status == "device_unverified"
    assert result.rows[0].observed == "unknown"
    assert result.rows[0].executable is False


@pytest.mark.parametrize("invalid_scan", [
    "foreign_profile", "wrong_device", "wrong_uuid", "stale",
    "future_timestamp", "incomplete",
])
def test_invalid_scanner_output_never_claims_current_presence(
    tmp_path, monkeypatch, invalid_scan,
):
    db, root, book, mount = setup(tmp_path)
    valid = fake_scan(files={ARTIFACT: UsbObservedFile(
        relative_path=TARGET, sha256=DIGEST, regular_file=True,
    )})
    changed = {
        "foreign_profile": replace(valid, identity_id="bob"),
        "wrong_device": replace(valid, device_id="reader-unknown"),
        "wrong_uuid": replace(valid, fingerprint="uuid-mismatched-0001"),
        "stale": replace(valid, observed_at=NOW - 301),
        "future_timestamp": replace(valid, observed_at=NOW + 1),
        "incomplete": replace(valid, complete=False),
    }[invalid_scan]
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_: changed)
    monkeypatch.setattr(
        run, "attest_local_usb_source",
        lambda **_: pytest.fail("unverified USB scan must not hash local books"),
    )
    result = inspect(db, root, mount)
    assert result.status == "device_unverified"
    assert result.scan_attested is False
    assert result.observed_at is None
    assert result.executable is False
    assert len(result.rows) == 1
    assert result.rows[0].observed == "unknown"
    assert result.rows[0].action == "verify"


def test_exact_identity_scope_blocks_cross_profile_without_ever_scanning(
    tmp_path, monkeypatch,
):
    db, root, book, mount = setup(tmp_path)
    def forbid(**kwargs):
        raise AssertionError("foreign reader must not be scanned")
    monkeypatch.setattr(run, "scan_managed_usb", forbid)
    result = run.inspect_usb_dry_run(
        storage=db, identity_id="bob", device_id="reader-a",
        identity_root=tmp_path / "bob", mountpoint=mount, now=NOW,
    )
    assert result.status == "scope_unverified"
    assert result.rows == ()
    assert result.executable is False


def test_legacy_foreign_intent_must_not_expose_other_owners_artifact(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    db.device_placement_put({
        "device_id": "reader-a", "artifact_id": ARTIFACT,
        "desired_state": "keep", "observed_state": "present",
        "target_relative_path": TARGET, "last_observed_at": 1700000000,
        "metadata": {"intent_identity_id": "bob"},
    })
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_:
                        pytest.fail("legacy foreign scope should stop before scan"))
    result = inspect(db, root, mount)
    assert result.status == "scope_unverified"
    assert result.rows == ()
    assert ARTIFACT not in repr(result)


@pytest.mark.parametrize("change", ["device_changed", "desired_changed", "target_changed"])
def test_mid_scan_rebind_or_intent_mutation_discards_all_rows(tmp_path, monkeypatch, change):
    db, root, book, mount = setup(tmp_path)
    def altered(**kwargs):
        if change == "device_changed":
            db.device_binding_put({
                **db.device_binding_get("reader-a"),
                "identity_id": "bob",
                "metadata": {"usb_fingerprint": "uuid-bob-9000"},
            })
        elif change == "desired_changed":
            db.device_usb_intent_set(
                identity_id="alice", device_id="reader-a",
                artifact_id=ARTIFACT, desired_state="remove",
            )
        else:
            db.device_placement_put({
                "device_id": "reader-a", "artifact_id": ARTIFACT,
                "desired_state": "keep", "observed_state": "unknown",
                "target_relative_path": "Books/Cifereca/moved.epub",
                "metadata": {"intent_identity_id": "alice"},
            })
        return fake_scan()
    monkeypatch.setattr(run, "scan_managed_usb", altered)
    result = inspect(db, root, mount)
    assert result.status == "scope_changed"
    assert result.rows == ()
    assert result.executable is False


def test_fail_closed_on_scan_exception_without_device_write(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    def unavailable(**kwargs):
        raise OSError("bad USB / endpoint")
    monkeypatch.setattr(run, "scan_managed_usb", unavailable)
    result = inspect(db, root, mount)
    assert result.status == "scope_unverified"
    assert result.rows == ()


def test_no_intentions_stops_before_scanning(tmp_path, monkeypatch):
    db, root, book, mount = setup(tmp_path)
    db.device_placement_delete("reader-a", ARTIFACT)
    monkeypatch.setattr(run, "scan_managed_usb", lambda **_:
                        pytest.fail("no intentions means no scan"))
    result = inspect(db, root, mount)
    assert result.status == "no_intentions"
    assert result.rows == ()


def test_no_explicit_hardware_validation_is_claimed(tmp_path):
    db, root, book, mount = setup(tmp_path)
    # A temp directory is not an independently attested Linux USB mount.
    # The real scanner rejects it; no actual hardware is used in this test.
    mount.mkdir()
    result = inspect(db, root, mount)
    assert result.status in ("device_unverified", "scope_unverified")
    assert result.scan_attested is False
    assert all(row.observed == "unknown" for row in result.rows)
