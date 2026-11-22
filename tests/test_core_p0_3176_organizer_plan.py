"""#3176: dry-run selection, collision and confirmation over actual Core."""
from pathlib import Path

import pytest

from cifereca.core.library_organizer_plan import (
    prepare_organizer_dry_run, confirm_organizer_dry_run,
)
from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from tests.test_core_p0_3171_atomic_publication_v14 import store, register, rows
from tests.test_core_p0_3176_organizer_review import _service, _put_epub, _run, META
from tests.test_p1_4496_keeper_review import setup_copies


def test_unified_review_confirms_existing_rename_proposal_without_writer(store, tmp_path, monkeypatch):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    path, pid = _put_epub(store, root, "Acquisitions/old.epub")
    service = _service(store, root)
    scope, page = _run(service)
    before, content = rows(store), path.read_bytes()
    calls = []
    lookups = []
    original_entries = store.catalog_entries

    def scoped_entries(**kwargs):
        assert kwargs == {"placement_id": pid, "limit": 1}
        lookups.append(kwargs)
        return original_entries(**kwargs)

    monkeypatch.setattr(store, "catalog_entries", scoped_entries)
    original = LibraryMaintenanceBrokerImpl.plan

    def observed(self, request):
        calls.append(request.operation)
        return original(self, request)

    monkeypatch.setattr(LibraryMaintenanceBrokerImpl, "plan", observed)
    monkeypatch.setattr(LibraryMaintenanceBrokerImpl, "run",
                        lambda *_a, **_kw: pytest.fail("dry-run executed"))
    assert prepare_organizer_dry_run(scope, page) is None
    plan = prepare_organizer_dry_run(scope, page, confirmed=True)
    assert plan is not None and not plan.executable and not plan.confirmed
    rename, = [e for e in plan.entries if e.operation == "rename"]
    assert rename.placement_id == pid and rename.state == "review_only"
    assert rename.proposed == "Acquisitions/Autrice - Roman commun.epub"
    assert confirm_organizer_dry_run(scope, page, plan) is None
    accepted = confirm_organizer_dry_run(scope, page, plan, confirmed=True)
    assert accepted.confirmed and not accepted.executable
    assert calls == ["normalize_filename", "normalize_filename"]
    assert len(lookups) == 2
    assert rows(store) == before and path.read_bytes() == content
    assert not (path.parent / Path(rename.proposed).name).exists()


@pytest.mark.parametrize("change", ["sqlite", "collision", "bytes", "navigation"])
def test_dry_run_rejects_changed_page_before_confirmation(store, tmp_path, change):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    path, _ = _put_epub(store, root, "Acquisitions/old.epub")
    service = _service(store, root)
    scope, page = _run(service)
    plan = prepare_organizer_dry_run(scope, page, confirmed=True)
    assert plan is not None
    if change == "sqlite":
        store.metadata_set("organizer-dry-run-drift", "changed")
    elif change == "collision":
        path.with_name("Autrice - Roman commun.epub").write_bytes(b"other")
    elif change == "bytes":
        path.write_bytes(b"changed")
    else:
        service._account_context_epoch += 2
    before = rows(store)
    assert confirm_organizer_dry_run(scope, page, plan, confirmed=True) is None
    assert rows(store) == before and path.exists()


def test_keeper_entries_are_explicitly_held(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, (
        "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
    ))
    scope, page = _run(_service(store, root))
    before = rows(store)
    plan = prepare_organizer_dry_run(scope, page, confirmed=True)
    entry, = plan.entries
    assert entry.operation == "keeper" and entry.state == "cleanup_held"
    assert entry.placement_id == pair[0].placement_id
    assert not plan.executable
    assert rows(store) == before


def test_two_independent_renames_to_one_vacant_target_are_held(store, tmp_path):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    # Distinct EPUB bytes but the same descriptive title and author.
    one, _ = _put_epub(store, root, "Acquisitions/one.epub")
    two, _ = _put_epub(store, root, "Acquisitions/two.epub",
                      metadata=META + '<dc:language xmlns:dc="http://purl.org/dc/elements/1.1/">fr</dc:language>')
    assert one.read_bytes() != two.read_bytes()
    scope, page = _run(_service(store, root))
    plan = prepare_organizer_dry_run(scope, page, confirmed=True)
    renames = [e for e in plan.entries if e.operation == "rename"]
    assert len(renames) == 2
    assert {e.state for e in renames} == {"destination_conflict"}
    assert not plan.executable



def test_indexed_placement_scope_preserves_first_entry_order(store, tmp_path):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    path, pid = _put_epub(store, root, "Acquisitions/old.epub")
    other, other_pid = _put_epub(store, root, "Acquisitions/other.epub",
        metadata=META + '<dc:language xmlns:dc="http://purl.org/dc/elements/1.1/">fr</dc:language>')
    original = store.catalog_entries()
    expected = [entry for entry in original if entry["placement_id"] == pid][:1]
    assert len(expected) == 1
    assert store.catalog_entries(placement_id=pid, limit=1) == expected
    assert store.catalog_entries(placement_id="no-such-placement", limit=1) == []
    assert all(entry["placement_id"] == other_pid
               for entry in store.catalog_entries(placement_id=other_pid))
    with pytest.raises(ValueError):
        store.catalog_entries(placement_id=True, limit=1)

@pytest.mark.parametrize("selection", [0, 1, -1, 2, True, "0"])
def test_single_group_selection_preserves_page_conflicts(store, tmp_path, selection):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    _put_epub(store, root, "Acquisitions/one.epub")
    _put_epub(store, root, "Acquisitions/two.epub",
              metadata=META + '<dc:language xmlns:dc="http://purl.org/dc/elements/1.1/">fr</dc:language>')
    scope, page = _run(_service(store, root))
    before = rows(store)
    selected = prepare_organizer_dry_run(scope, page, confirmed=True, group_index=selection)
    if type(selection) is int and selection in (0, 1):
        assert selected.group_index == selection and not selected.executable
        entry, = selected.entries
        assert entry.placement_id == page["groups"][selection]["files"][0]["placement"]
        assert entry.state == "destination_conflict"
        final = confirm_organizer_dry_run(scope, page, selected, confirmed=True)
        assert final.confirmed and final.group_index == selection and final.entries == selected.entries
        assert not final.executable
    else:
        assert selected is None
    assert rows(store) == before


def test_rename_only_filters_after_collision_detection_and_rechecks_exact_selection(store, tmp_path):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    one, _ = _put_epub(store, root, "Acquisitions/one.epub")
    two, _ = _put_epub(store, root, "Acquisitions/two.epub", metadata=META + '<dc:language xmlns:dc="http://purl.org/dc/elements/1.1/">fr</dc:language>')
    three, pid = _put_epub(store, root, "Acquisitions/three.epub",
                           metadata=META.replace("Roman commun", "Roman unique"))
    # The shared fixture uses a fixed catalogue title even for custom OPF.
    # A distinct safe proposal needs matching independent catalogue evidence.
    with store._lock, store._connect() as db:
        db.execute("UPDATE catalog_entries SET title=? WHERE placement_id=?", ("Roman unique", pid))
    scope, page = _run(_service(store, root))
    before = rows(store)
    all_plan = prepare_organizer_dry_run(scope, page, confirmed=True)
    plan = prepare_organizer_dry_run(scope, page, confirmed=True, rename_only=True)
    assert plan.rename_only and not plan.executable and plan.group_index is None
    assert plan.excluded_count == len(all_plan.entries) - 1 == 2
    entry, = plan.entries
    assert entry.placement_id == pid and entry.state == "review_only"
    assert entry.operation == "rename" and entry.proposed.endswith("Autrice - Roman unique.epub")
    assert prepare_organizer_dry_run(scope, page, confirmed=True, rename_only=1) is None
    assert prepare_organizer_dry_run(scope, page, confirmed=True,
                                     rename_only=True, group_index=0) is None
    accepted = confirm_organizer_dry_run(scope, page, plan, confirmed=True)
    assert accepted.confirmed and accepted.rename_only and not accepted.executable
    assert accepted.entries == plan.entries and accepted.excluded_count == 2
    assert rows(store) == before and all(path.exists() for path in (one, two, three))
    three.with_name("Autrice - Roman unique.epub").write_bytes(b"colliding target")
    assert confirm_organizer_dry_run(scope, page, plan, confirmed=True) is None


def test_rename_only_empty_never_has_confirmation(store, tmp_path):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    _put_epub(store, root, "Acquisitions/one.epub")
    _put_epub(store, root, "Acquisitions/two.epub", metadata=META + '<dc:language xmlns:dc="http://purl.org/dc/elements/1.1/">fr</dc:language>')
    scope, page = _run(_service(store, root))
    plan = prepare_organizer_dry_run(scope, page, confirmed=True, rename_only=True)
    assert plan is not None and plan.rename_only and plan.entries == () and plan.excluded_count == 2
    assert confirm_organizer_dry_run(scope, page, plan, confirmed=True) is None
