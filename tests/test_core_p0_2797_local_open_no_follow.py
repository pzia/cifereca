"""P0 #2797: local book open must not follow paths into another profile.

Offline disposable SQLite/files; no provider call or file mutation by Core.
Registered default and separate Kobo root remain eligible.
"""
from pathlib import Path

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_core0925_open_destinations import _placement, _service


def _owned(service, tmp_path: Path, *, owner="alice"):
    path = tmp_path / "EBooks" / owner / "local" / "Book.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic local file")
    pid = _placement(service, path, identity_id=owner)
    return pid, path


def test_exact_owner_regular_book_can_still_open(tmp_path):
    service = _service(tmp_path)
    try:
        pid, path = _owned(service, tmp_path)
        assert service.local_book_path(pid, identity_id="alice") == path.resolve()
        with pytest.raises(ProviderConfigurationError):
            service.local_book_path(pid, identity_id="bob")
    finally:
        service.close()


def test_symlink_replacing_existing_book_cannot_open_other_profile_file(tmp_path):
    service = _service(tmp_path)
    try:
        pid, path = _owned(service, tmp_path)
        _foreign_pid, foreign = _owned(service, tmp_path, owner="bob")
        path.unlink()
        path.symlink_to(foreign)
        with pytest.raises(ProviderConfigurationError):
            service.local_book_path(pid, identity_id="alice")
        assert foreign.is_file()
        assert foreign.read_bytes() == b"synthetic local file"
    finally:
        service.close()


def test_symlink_replacing_ancestor_cannot_open_other_profile(tmp_path):
    service = _service(tmp_path)
    try:
        pid, path = _owned(service, tmp_path)
        _foreign_pid, foreign = _owned(service, tmp_path, owner="bob")
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(foreign.parent, target_is_directory=True)
        with pytest.raises(ProviderConfigurationError):
            service.local_book_path(pid, identity_id="alice")
    finally:
        service.close()


def test_imported_placement_claiming_foreign_path_is_not_openable(tmp_path):
    service = _service(tmp_path)
    try:
        foreign = tmp_path / "EBooks" / "bob" / "local" / "Private.epub"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes(b"private Bob")
        # A corrupt/imported row can claim an Alice owner of a Bob path, even
        # without a symlink. The app must not treat this as Alice's own file.
        pid = _placement(
            service, foreign, identity_id="alice",
            artifact_id="sha256:unattested-import",
            source_item_id="foreign-claim",
        )
        with pytest.raises(ProviderConfigurationError):
            service.local_book_path(pid, identity_id="alice")
    finally:
        service.close()


def test_configured_separate_kobo_owner_root_remains_openable(tmp_path):
    service = _service(tmp_path)
    try:
        backup = tmp_path / "private-kobo-target"
        service.config.set("kobo_backup.directory", str(backup))
        path = backup / "alice" / "kobo" / "Book.epub"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"synthetic Kobo file")
        pid = _placement(service, path)
        assert service.local_book_path(pid, identity_id="alice") == path.resolve()
    finally:
        service.close()


def test_legacy_unowned_file_is_default_only_but_not_inside_bob_root(tmp_path):
    service = _service(tmp_path)
    try:
        legacy = tmp_path / "EBooks" / "legacy" / "Old.epub"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"old local file")
        pid = service.storage.placement_put({
            "artifact_id": "sha256:legacy", "path": str(legacy),
            "identity_id": None, "state": "present",
        })
        assert service.local_book_path(pid, identity_id="alice") == legacy.resolve()
        with pytest.raises(ProviderConfigurationError):
            service.local_book_path(pid, identity_id="bob")

        # Historical NULL ownership must never adopt an explicitly registered
        # different profile's folder, even if its row points there.
        bob = tmp_path / "EBooks" / "bob" / "local" / "Private.epub"
        bob.parent.mkdir(parents=True, exist_ok=True)
        bob.write_bytes(b"private Bob")
        poisoned = service.storage.placement_put({
            "artifact_id": "sha256:legacy-bob", "path": str(bob),
            "identity_id": None, "state": "present",
        })
        with pytest.raises(ProviderConfigurationError):
            service.local_book_path(poisoned, identity_id="alice")
    finally:
        service.close()


def test_explicitly_owned_external_legacy_file_remains_openable(tmp_path):
    service = _service(tmp_path)
    try:
        external = tmp_path / "manual-drive" / "alice-book.epub"
        external.parent.mkdir(parents=True)
        external.write_bytes(b"explicit manual import")
        pid = _placement(service, external, identity_id="alice")
        assert service.local_book_path(pid, identity_id="alice") == external.resolve()
        with pytest.raises(ProviderConfigurationError):
            service.local_book_path(pid, identity_id="bob")
    finally:
        service.close()
