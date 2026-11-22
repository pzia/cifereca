"""#6937: identity directory migration never traverses Core derived staging."""

from pathlib import Path

import pytest

from cifereca.identities import directory_migration as migration
from cifereca.core.derived_variant_region_paths import PRIVATE_DERIVED_REGION


def test_legacy_migration_preflight_rejects_stage_without_entering_it(
    tmp_path, monkeypatch,
):
    root = tmp_path / "alice"
    stage_region = root / "nested" / PRIVATE_DERIVED_REGION
    stage_region.mkdir(parents=True)
    stage = stage_region / "unpublished.epub"
    stage.write_bytes(b"untouched stage")
    ordinary = root / "ordinary.epub"
    ordinary.write_bytes(b"ordinary")
    real_scandir = migration.os.scandir

    def guarded(path):
        if not isinstance(path, int):
            assert PRIVATE_DERIVED_REGION not in Path(path).parts
        return real_scandir(path)

    monkeypatch.setattr(migration.os, "scandir", guarded)
    with pytest.raises(migration.DirectoryMigrationError, match="Zone privée"):
        migration.checked_tree(root)
    with pytest.raises(migration.DirectoryMigrationError, match="Zone privée"):
        migration.fingerprint(root)
    assert ordinary.read_bytes() == b"ordinary"
    assert stage.read_bytes() == b"untouched stage"


def test_migration_preflight_allows_lookalike_but_refuses_nested_symlink(tmp_path):
    root = tmp_path / "alice"
    sibling = root / (PRIVATE_DERIVED_REGION + "-archive")
    sibling.mkdir(parents=True)
    user_file = sibling / "user.epub"
    user_file.write_bytes(b"ordinary")
    migration.checked_tree(root)
    assert migration.fingerprint(root).startswith("directory:")
    outsider = tmp_path / "other.epub"
    outsider.write_bytes(b"foreign")
    (sibling / "link.epub").symlink_to(outsider)
    with pytest.raises(migration.DirectoryMigrationError, match="Entrée non régulière"):
        migration.checked_tree(root)
    assert outsider.read_bytes() == b"foreign"
    assert user_file.read_bytes() == b"ordinary"


def test_migration_preflight_scan_error_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "alice"
    root.mkdir()

    def unavailable(_path, **options):
        options["onerror"](PermissionError("synthetic denied"))
        yield

    monkeypatch.setattr(migration.os, "walk", unavailable)
    with pytest.raises(migration.DirectoryMigrationError, match="inaccessible"):
        migration.checked_tree(root)
