"""#6937: legacy reconciliation must not inspect the private derived region."""
from pathlib import Path

import pytest

from cifereca.core import library_reconciliation_walk as walk
from cifereca.core.derived_variant_region_paths import PRIVATE_DERIVED_REGION


def test_reserved_region_skipped_before_stat_open_and_budget(tmp_path, monkeypatch):
    root = tmp_path / "identity"
    private = root / "nested" / PRIVATE_DERIVED_REGION
    ordinary = root / "nested" / (PRIVATE_DERIVED_REGION + "-old")
    private.mkdir(parents=True)
    ordinary.mkdir()
    (private / "stage.epub").write_bytes(b"private stage")
    (ordinary / "user.pdf").write_bytes(b"ordinary")
    (root / "book.epub").write_bytes(b"book")
    before = (private / "stage.epub").read_bytes()
    real_stat, real_open = walk.os.stat, walk.os.open

    def guarded_stat(path, *args, **kwargs):
        assert path != PRIVATE_DERIVED_REGION, "private stage was inspected"
        return real_stat(path, *args, **kwargs)

    def guarded_open(path, *args, **kwargs):
        assert path != PRIVATE_DERIVED_REGION, "private stage was opened"
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(walk.os, "stat", guarded_stat)
    monkeypatch.setattr(walk.os, "open", guarded_open)
    result = walk.walk_identity_root(root)
    assert result.complete, result.reasons
    assert {x.path.relative_to(root).as_posix() for x in result.candidates} == {
        "book.epub", "nested/" + PRIVATE_DERIVED_REGION + "-old/user.pdf"}
    assert result.entries_inspected == 4
    assert result.directories_inspected == 3
    assert (private / "stage.epub").read_bytes() == before
    assert result.redacted_summary()["mutation_authorized"] is False


@pytest.mark.parametrize("relative", [
    PRIVATE_DERIVED_REGION,
    "nested/" + PRIVATE_DERIVED_REGION,
    PRIVATE_DERIVED_REGION + "/leaf",
])
def test_reserved_region_cannot_be_explicit_reconciliation_root(tmp_path, relative):
    source = tmp_path / relative
    source.mkdir(parents=True)
    (source / "stage.epub").write_bytes(b"fixture")
    with pytest.raises(ValueError, match="invalid_reconciliation_identity_root"):
        walk.walk_identity_root(source)
    assert (source / "stage.epub").read_bytes() == b"fixture"


def test_nearby_nonreserved_root_still_supported(tmp_path):
    source = tmp_path / (PRIVATE_DERIVED_REGION + "-backup")
    source.mkdir()
    (source / "local.epub").write_bytes(b"ordinary")
    result = walk.walk_identity_root(source)
    assert result.complete and [p.path.name for p in result.candidates] == ["local.epub"]
