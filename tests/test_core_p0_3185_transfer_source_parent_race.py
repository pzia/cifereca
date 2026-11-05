"""P0 #3185: sending from a mutable parent path must not read Bob's bytes.

A legacy source is indexed under Alice. After its initial SHA/path checks,
a concurrent actor redirects Alice's parent directory to Bob's directory.
The two files deliberately have IDENTICAL bytes: a plain SHA re-check would
not detect a foreign source. No provider, DRM or real user data.
"""
from pathlib import Path

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_core0925_open_destinations import _placement, _service


@pytest.mark.parametrize("same_contents", [True, False])
def test_substituted_intermediate_parent_never_sends_foreign_profile_file(
    tmp_path, monkeypatch, same_contents,
):
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    owned = root / "alice" / "local" / "Book.epub"
    foreign = root / "bob" / "local" / "Book.epub"
    owned.parent.mkdir(parents=True)
    foreign.parent.mkdir(parents=True)
    original = b"identical synthetic book bytes"
    different = b"other profile secret that must not be read"
    owned.write_bytes(original)
    foreign.write_bytes(original if same_contents else different)
    pid = _placement(
        service, owned, identity_id="alice", artifact_id="sha256:legacy",
    )
    original_catalogue_read = service.storage.catalog_entries
    moved = root / "alice" / "moved-local"

    def replace_parent_after_initial_hash(*args, **kwargs):
        if not moved.exists():
            owned.parent.rename(moved)
            owned.parent.symlink_to(foreign.parent, target_is_directory=True)
        return original_catalogue_read(*args, **kwargs)

    monkeypatch.setattr(
        service.storage, "catalog_entries", replace_parent_after_initial_hash,
    )
    try:
        with pytest.raises(
            ProviderConfigurationError, match="hors du profil propriétaire",
        ):
            service.materialize_to_identity(pid, "bob")
        assert moved.joinpath("Book.epub").read_bytes() == original
        assert foreign.read_bytes() == (original if same_contents else different)
        assert owned.parent.is_symlink()
        assert not list((root / "bob" / "Acquisitions").glob("Book*.epub"))
        assert not any(
            row.get("identity_id") == "bob"
            for row in service.storage.placements()
        )
        assert service.storage.entitlements() == []
    finally:
        service.close()


def test_normal_nested_source_remains_sendable_without_provider_rights(tmp_path):
    service = _service(tmp_path)
    source = tmp_path / "EBooks" / "alice" / "local" / "nested" / "Book.epub"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"ordinary local synthetic source")
    pid = _placement(
        service, source, identity_id="alice", artifact_id="sha256:legacy",
    )
    try:
        result = service.materialize_to_identity(pid, "bob")
        assert Path(result["path"]).read_bytes() == source.read_bytes()
        assert service.storage.entitlements() == []
        assert service.storage.placement_get(result["placement_id"])["identity_id"] == "bob"
    finally:
        service.close()
