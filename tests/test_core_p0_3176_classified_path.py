"""#3176: classified destination is layout advice only, never a relocation."""
import pytest

from cifereca.gui.library_organizer_line import _display
from tests.test_core_p0_3171_atomic_publication_v14 import store, register, rows
from tests.test_core_p0_3176_organizer_review import _put_epub, _service, _run
from tests.test_core_p0_4030_epub_evidence import META

TYPED = META + '<dc:type xmlns:dc="http://purl.org/dc/elements/1.1/">https://schema.org/Book</dc:type>'


@pytest.mark.parametrize("state,reason,expected", [
    ("missing", "classified_directory_missing", True),
    ("available", "classified_layout_advice_only", True),
    ("file", "classified_target_occupied", False),
    ("sql", "classified_target_occupied", False),
    ("alias", "classified_target_unverified", False),
    ("nondirectory", "classified_target_unverified", False),
    ("history", "classified_source_not_admitted", False),
    ("unclassified", "classified_evidence_unavailable", False),
    ("duplicate", None, False),
])
def test_classified_path_only_for_unique_verified_acquisition(
    store, tmp_path, state, reason, expected,
):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    source, pid = _put_epub(
        store, root, "shared/old.epub" if state == "history" else "Acquisitions/old.epub",
        metadata=META if state == "unclassified" else TYPED,
    )
    target = root / "A" / "Livres" / "Autrice" / "Autrice - Roman commun.epub"
    if state in ("available", "file"):
        target.parent.mkdir(parents=True)
        if state == "file":
            target.write_bytes(b"foreign bytes")
    elif state == "sql":
        # A missing path is still reserved by an operational Placement.
        store.placement_put({"identity_id": "A", "state": "missing", "path": str(target)})
    elif state == "alias":
        external = tmp_path / "external"
        external.mkdir()
        (root / "A" / "Livres").symlink_to(external, target_is_directory=True)
    elif state == "nondirectory":
        (root / "A" / "Livres").write_bytes(b"not a directory")
    elif state == "duplicate":
        duplicate = source.with_name("copy.epub")
        duplicate.write_bytes(source.read_bytes())
        store.placement_put({"identity_id": "A", "state": "present",
                             "artifact_id": store.placement_get(pid)["artifact_id"],
                             "path": str(duplicate)})
    before = rows(store)
    tree = sorted(str(p) for p in root.rglob("*"))
    original = source.read_bytes()
    _, result = _run(_service(store, root))
    file = next(f for group in result["groups"] for f in group["files"] if f["placement"] == pid)
    assert file["classified_path_reason"] == reason
    proposed = "Livres/Autrice/Autrice - Roman commun.epub"
    assert file["classified_path_advice"] == (proposed if expected else None)
    assert (proposed in _display(result)) is expected
    assert not any(group["relocation_proposed"] for group in result["groups"])
    assert rows(store) == before and tree == sorted(str(p) for p in root.rglob("*"))
    assert source.read_bytes() == original
    if state == "file":
        assert target.read_bytes() == b"foreign bytes"


@pytest.mark.parametrize("dependency", ["none", "relative_path", "checkpoint"])
def test_classified_target_existing_sqlite_relocation_boundary_isolated(
    store, tmp_path, dependency,
):
    """Qualify only the existing SQL writer, not a classification command.

    Test setup stages a hardlink on the same filesystem. Both names remain
    throughout: no production executor, cleanup, or relocation authority.
    """
    import json
    import os
    from cifereca.core.library_organizer_plan import (
        prepare_organizer_dry_run, confirm_organizer_dry_run,
    )

    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    old, pid = _put_epub(store, root, "Acquisitions/Autrice - Roman commun.epub",
                         metadata=TYPED)
    scope, page = _run(_service(store, root))
    plan = prepare_organizer_dry_run(scope, page, confirmed=True)
    accepted = confirm_organizer_dry_run(scope, page, plan, confirmed=True)
    assert accepted.confirmed and not accepted.executable
    entry, = (e for e in accepted.entries if e.operation == "classify")
    assert entry.placement_id == pid and entry.state == "relocation_held"
    target = root / "A" / entry.proposed
    assert target == root / "A/Livres/Autrice/Autrice - Roman commun.epub"
    # Separate synthetic SQL qualification: bind the fixture explicitly.
    # This invalidates the held plan; it is never a writer authorization.
    with store._lock, store._connect() as db:
        db.execute("UPDATE placements SET provider_id=?,source_item_id=? WHERE placement_id=?",
                   ("local", "fixture-item", pid))
        db.execute("UPDATE catalog_entries SET provider_id=?,item_id=? WHERE placement_id=?",
                   ("local", "fixture-item", pid))
    assert confirm_organizer_dry_run(scope, page, plan, confirmed=True) is None
    if dependency == "relative_path":
        with store._lock, store._connect() as db:
            db.execute("UPDATE placements SET metadata_json=? WHERE placement_id=?",
                       (json.dumps({"relative_path": "Acquisitions/Autrice - Roman commun.epub"}), pid))
    elif dependency == "checkpoint":
        store.acquisition_put({
            "provider_id": "local", "credential_id": "fixture-account",
            "identity_id": "A", "item_id": "fixture-checkpoint",
            "status": "attention_required",
            "metadata": {"phase": "index_pending", "path": str(old)},
        })
    baseline, data = store.placement_get(pid), old.read_bytes()
    before = rows(store)
    history = store.acquisitions()
    # Fixture-only staging: the production organizer has no such command.
    target.parent.mkdir(parents=True)
    os.link(old, target)
    if dependency == "none":
        store.relocate_local_path(old, target, identity_id="A", provider_id="local")
        current = store.placement_get(pid)
        assert current["path"] == str(target)
        for key in ("artifact_id", "identity_id", "provider_id", "source_item_id", "state"):
            assert current[key] == baseline[key]
        assert store.placement_for_path(old) is None
        assert all(e["metadata"]["path"] == str(target)
                   for e in store.catalog_entries(placement_id=pid))
    else:
        code = ("relocate_relative_path_requires_relink" if dependency == "relative_path"
                else "relocate_acquisition_reference_requires_relink")
        with pytest.raises(ValueError, match=code):
            store.relocate_local_path(old, target, identity_id="A", provider_id="local")
        assert store.placement_get(pid) == baseline
        assert store.placement_for_path(target) is None
    assert old.samefile(target) and old.read_bytes() == target.read_bytes() == data
    assert rows(store) == before and store.acquisitions() == history
    assert store.entitlements() == []
