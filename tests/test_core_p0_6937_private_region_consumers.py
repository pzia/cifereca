"""#6937: ordinary consumers never treat the v1 region as book stock."""
from pathlib import Path

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.core.canonical_dedup_exclusions import ordinary_dedup_path_excluded
from cifereca.core.derived_variant_region_paths import PRIVATE_DERIVED_REGION
from cifereca.core.library_inventory import scan_identity_roots
from tests.test_core_p0_3171_atomic_publication_v14 import store, register, BYTES


def test_catalogue_excludes_reserved_region_without_missing_transition(store, tmp_path, monkeypatch):
    root = tmp_path / "EBooks"
    register(store, "A", "seller", "card-A")
    region = root / "A" / PRIVATE_DERIVED_REGION
    region.mkdir(parents=True)
    stage = region / "pending.pdf"
    stage.write_bytes(BYTES)
    from cifereca.plugin_api import BUNDLE_MANIFEST_NAME
    manifest = region / "album" / BUNDLE_MANIFEST_NAME
    manifest.parent.mkdir()
    manifest.write_text("{}")
    ordinary = root / "A" / "ordinary.pdf"
    ordinary.write_bytes(BYTES)
    pid = store.placement_put({"path": str(stage), "identity_id": "A",
                              "state": "present", "artifact_id": None})
    catalog = LocalCatalog(store, root)
    validate = catalog._validate
    def guarded(path, *args):
        assert PRIVATE_DERIVED_REGION not in path.parts
        return validate(path, *args)
    monkeypatch.setattr(catalog, "_validate", guarded)
    monkeypatch.setattr(catalog, "_index_bundle",
                        lambda *a, **k: pytest.fail("private bundle indexed"))
    assert catalog.index_path(stage)["reason"] == "canonical_staging_not_published"
    assert catalog.index_path(region)["reason"] == "canonical_staging_not_published"
    result = catalog.scan()
    assert result.cataloged == 1 and result.missing_marked == 0
    assert store.placement_get(pid)["state"] == "present"
    assert len(store.catalog_entries()) == 1
    assert stage.read_bytes() == ordinary.read_bytes() == BYTES
    assert manifest.read_text() == "{}"


def test_inventory_excludes_reserved_namespace_but_not_similar_names(tmp_path, monkeypatch):
    from cifereca.core import library_inventory as inventory
    root = tmp_path / "A"
    for directory in (PRIVATE_DERIVED_REGION, PRIVATE_DERIVED_REGION + "-backup", "nested"):
        folder = root / directory
        folder.mkdir(parents=True)
        (folder / "book.pdf").write_bytes(BYTES)
    private = root / "nested" / PRIVATE_DERIVED_REGION
    private.mkdir()
    (private / "book.pdf").write_bytes(BYTES)
    original = inventory.os.open
    def guarded(path, *args, **kwargs):
        assert str(path) != PRIVATE_DERIVED_REGION
        return original(path, *args, **kwargs)
    monkeypatch.setattr(inventory.os, "open", guarded)
    result = scan_identity_roots({"A": (root,)})
    assert {Path(row.path).parent.name for row in result.files} == {
        PRIVATE_DERIVED_REGION + "-backup", "nested",
    }
    assert result.files_started == 2
    with pytest.raises(ValueError, match="invalid_inventory_roots"):
        scan_identity_roots({"A": (private,)})


@pytest.mark.parametrize("subpath", [PRIVATE_DERIVED_REGION, "nested/" + PRIVATE_DERIVED_REGION])
def test_ordinary_dedup_refuses_private_stage_names(tmp_path, subpath):
    root = tmp_path / "EBooks"
    owner = root / "A"
    stage = owner / subpath / "candidate.pdf"
    other = owner / (PRIVATE_DERIVED_REGION + "-backup") / "candidate.pdf"
    options = dict(library_root=root, identity_id="A", owner_directory="A")
    assert ordinary_dedup_path_excluded(stage, **options)
    assert not ordinary_dedup_path_excluded(other, **options)


def test_private_region_does_not_consume_ordinary_scan_entry_budget(tmp_path):
    """A reserved stage cannot displace a real book under a tight scan budget."""
    from cifereca.core.library_inventory import InventoryLimits

    root = tmp_path / "identity"
    private = root / PRIVATE_DERIVED_REGION
    private.mkdir(parents=True)
    stage = private / "pending.epub"
    stage.write_bytes(BYTES)
    ordinary = root / "book.pdf"
    ordinary.write_bytes(b"%PDF-1.4\nsynthetic local book\n")
    result = scan_identity_roots({"A": (root,)}, limits=InventoryLimits(entries=1))
    assert result.entries == 1
    assert result.files_started == 1
    assert {Path(observed.path).name for observed in result.files} == {"book.pdf"}
    assert "entry_budget" not in result.reasons
    assert stage.read_bytes() == BYTES


def test_private_region_cannot_supply_usb_source_or_snapshot(tmp_path, monkeypatch):
    from hashlib import sha256

    from cifereca.core.device_usb_content_snapshot import snapshot_local_usb_content
    from cifereca.core.device_usb_local_source import attest_local_usb_source

    root = tmp_path / "alice"
    private = root / PRIVATE_DERIVED_REGION / "book.epub"
    ordinary = root / (PRIVATE_DERIVED_REGION + "-backup") / "book.epub"
    for path in (private, ordinary):
        path.parent.mkdir(parents=True)
        path.write_bytes(BYTES)
    digest = sha256(BYTES).hexdigest()
    artifact = {"artifact_id": "sha256:" + digest, "sha256": digest,
                "kind": "file", "size": len(BYTES)}
    placement = {"artifact_id": artifact["artifact_id"], "identity_id": "alice",
                 "state": "present", "path": str(ordinary)}
    scope = dict(identity_id="alice", identity_root=root, artifact=artifact)
    admitted = attest_local_usb_source(placement=placement, **scope)
    assert admitted is not None
    assert snapshot_local_usb_content(placement=placement, local=admitted, **scope) == BYTES

    lstat = Path.lstat

    def refuse_probe(path, *args, **kwargs):
        assert PRIVATE_DERIVED_REGION not in path.parts
        return lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", refuse_probe)
    placement["path"] = str(private)
    assert attest_local_usb_source(placement=placement, **scope) is None
    assert snapshot_local_usb_content(placement=placement, local=admitted, **scope) is None
    assert private.read_bytes() == BYTES


def test_private_missing_placement_cannot_enter_ordinary_retirement(store, tmp_path):
    from cifereca.core.missing_placement_readiness import preview_missing_placement_retirement
    from cifereca.core.missing_placement_retirement import (
        prepare_missing_placement_retirement, retire_missing_placement,
    )

    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    rows = {}
    for directory in (PRIVATE_DERIVED_REGION, PRIVATE_DERIVED_REGION + "-backup"):
        path = root / "A" / directory / "absent.epub"
        path.parent.mkdir(parents=True)
        rows[directory] = store.placement_put({
            "path": str(path), "identity_id": "A", "state": "missing", "artifact_id": None,
        })
    private = rows[PRIVATE_DERIVED_REGION]
    ordinary = rows[PRIVATE_DERIVED_REGION + "-backup"]
    scope = dict(identity_id="A", library_root=root)
    before = store.placement_get(private)
    assert preview_missing_placement_retirement(
        store, placement_id=private, **scope,
    )["reason"] == "path_out_of_scope"
    assert prepare_missing_placement_retirement(store, placement_id=private, **scope) == {
        "status": "blocked", "reason": "path_out_of_scope",
    }
    assert retire_missing_placement(
        store, placement_id=private, plan_token="a" * 64, confirmed=True, **scope,
    ) == {"status": "blocked", "reason": "path_out_of_scope"}
    assert prepare_missing_placement_retirement(
        store, placement_id=ordinary, **scope,
    )["status"] == "ready"
    assert store.placement_get(private) == before
