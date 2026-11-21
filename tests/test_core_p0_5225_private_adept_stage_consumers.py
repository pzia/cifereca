"""#5225: reserved ADEPT work names are invisible to ordinary catalogue consumers."""
from pathlib import Path

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.core.canonical_dedup_exclusions import ordinary_dedup_path_excluded
from tests.test_core_p0_3171_atomic_publication_v14 import store, register, BYTES


@pytest.mark.parametrize("prefix", [".adept-stage-", ".adept-publish-"])
def test_private_adept_stage_is_not_indexed_or_marked_missing(store, tmp_path, monkeypatch, prefix):
    root = tmp_path / "EBooks"
    register(store, "A", "seller", "card-A")
    owner = root / "A"
    owner.mkdir(parents=True)
    stage = owner / (prefix + "a" * 32 + ".pdf")
    stage.write_bytes(BYTES)
    ordinary = owner / "ordinary.pdf"
    ordinary.write_bytes(BYTES)
    pid = store.placement_put({"path": str(stage), "identity_id": "A",
                              "state": "present", "artifact_id": None})
    catalog = LocalCatalog(store, root)
    validate = catalog._validate

    def guarded(path, *args):
        assert path != stage
        return validate(path, *args)

    monkeypatch.setattr(catalog, "_validate", guarded)
    assert catalog.index_path(stage)["reason"] == "canonical_staging_not_published"
    result = catalog.scan()
    assert result.cataloged == 1 and result.missing_marked == 0
    assert store.placement_get(pid)["state"] == "present"
    assert len(store.catalog_entries()) == 1
    assert stage.read_bytes() == ordinary.read_bytes() == BYTES
    options = dict(library_root=root, identity_id="A", owner_directory="A")
    assert ordinary_dedup_path_excluded(stage, **options)
    assert not ordinary_dedup_path_excluded(ordinary, **options)
