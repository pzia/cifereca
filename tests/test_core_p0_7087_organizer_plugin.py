"""#7087: standalone plugin loads headless and uses public DTOs only."""
from pathlib import Path
import importlib.util
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.core.library_organizer_broker import LibraryOrganizerReadBrokerImpl
from cifereca.plugin_api.guided_maintenance import GuidedMaintenanceTool
from tests.test_core_p0_3171_atomic_publication_v14 import store, rows, register
from tests.test_core_p0_3176_organizer_review import _service, _put_epub, META
from tests.test_p1_4496_keeper_review import setup_copies

ROOT = Path(__file__).resolve().parents[1] / "src/plugins/maintenance-library-organizer"


def _plugin(service):
    spec = importlib.util.spec_from_file_location("organizer_package_test", ROOT / "cifereca_library_organizer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    broker = LibraryOrganizerReadBrokerImpl(service)
    plugin = module.PLUGIN.activate(NS(broker=lambda key: broker if key == "library.organizer.read" else None))
    return module, plugin


def _host():
    tasks, renders = [], []
    host = NS(identity_id="A", live=True)
    host.is_current = lambda: host.live
    def submit(label, work, done):
        tasks.append((work, done))
        return True
    host.submit = submit
    return host, tasks, renders


def _finish(tasks):
    work, done = tasks[-1]
    result = work()
    done(result)
    done(result)


def test_manifest_and_public_pagination(store, tmp_path):
    manifest = json.loads((ROOT / "cifereca-plugin.json").read_text())
    descriptor, = manifest["plugins"]
    assert descriptor["type"] == "maintenance.tool" and descriptor["enabled_by_default"] is True
    assert descriptor["requires_brokers"] == ["library.organizer.read"]
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    for i in range(5):
        _put_epub(store, root, f"Acquisitions/{i}.epub",
                  metadata=META.replace("Roman commun", f"Roman {i}"))
    module, plugin = _plugin(_service(store, root))
    assert isinstance(plugin, GuidedMaintenanceTool)
    host, tasks, renders = _host()
    controller = plugin.controller(host, lambda *args: renders.append(args))
    before = rows(store)
    assert not tasks and not controller.request("next")
    assert controller.request("preview") and not controller.request("preview")
    _finish(tasks)
    assert controller.page.page == 1 and controller.page.has_more
    assert "aucun classement exécuté" in module.display(controller.page)
    assert controller.request("next")
    _finish(tasks)
    assert controller.page.page == 2 and not controller.page.has_more
    assert len(tasks) == 2 and len(renders) == 4 and rows(store) == before


@pytest.mark.parametrize("change", ["dispose", "disable", "identity", "route"])
@pytest.mark.parametrize("phase", ["before_worker", "after_worker"])
def test_stale_view_cannot_render_or_start_again(store, tmp_path, change, phase):
    root, _ = setup_copies(store, tmp_path, (
        "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
    ))
    _, plugin = _plugin(_service(store, root))
    host, tasks, renders = _host()
    controller = plugin.controller(host, lambda *args: renders.append(args))
    assert controller.request("preview")
    work, done = tasks[0]
    if phase == "after_worker":
        result = work()
        assert result is not None
    if change == "dispose":
        controller.dispose()
    elif change == "disable":
        plugin.deactivate()
    elif change == "identity":
        host.identity_id = "B"
    else:
        host.live = False
    if phase == "before_worker":
        result = work()
        assert result is None
    done(result)
    assert controller.page is None and not controller.request("preview")
    assert not any(page is not None for page, busy in renders)


def test_selection_uses_worker_and_never_writes(store, tmp_path):
    root, _ = setup_copies(store, tmp_path, (
        "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
    ))
    module, plugin = _plugin(_service(store, root))
    host, tasks, renders = _host()
    controller = plugin.controller(host, lambda *args: renders.append(args))
    before = rows(store)
    assert controller.request("preview")
    _finish(tasks)
    assert controller.request("select")
    _finish(tasks)
    assert controller.page.selected == 1 and not controller.page.mutation_authorized
    assert "1 sélectionnés" in module.display(controller.page)
    assert not controller.request("select") and rows(store) == before


@pytest.mark.parametrize("action", ["preview", "next", "select"])
@pytest.mark.parametrize("phase", ["before_worker", "after_worker"])
def test_cancel_revokes_organizer_selection_and_late_worker_result(
    store, tmp_path, action, phase,
):
    if action == "select":
        root, _ = setup_copies(store, tmp_path, (
            "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
        ))
    else:
        root = tmp_path / "library"
        register(store, "A", "seller", "card-A")
        for i in range(5):
            _put_epub(store, root, f"Acquisitions/cancel-{i}.epub",
                      metadata=META.replace("Roman commun", f"Roman cancel {i}"))
    _, plugin = _plugin(_service(store, root))
    host, tasks, renders = _host()
    c = plugin.controller(host, lambda *args: renders.append(args))
    before = rows(store)
    if action != "preview":
        assert c.request("preview")
        _finish(tasks)
        if action == "next":
            assert c.page.has_more
        else:
            assert c.page.eligible
    old = c.page
    assert c.request(action)
    work, done = tasks[-1]
    value = work() if phase == "after_worker" else None
    assert c.cancel() and not c.cancel()
    assert not c.request("preview"), "cancel does not detach a running worker"
    assert work() is None if phase == "before_worker" else True
    done(value)
    done(value)
    assert c.page is None and not c.busy and c.stop is None
    assert not any(page is not None for page, busy in renders[-2:])
    if old is not None:
        assert plugin.broker.next_page(token=old.token, confirmed=True) is None
    if value is not None:
        assert plugin.broker.next_page(token=value.token, confirmed=True) is None
    assert c.request("preview")
    _finish(tasks)
    assert c.page.page == 1 and rows(store) == before


@pytest.mark.parametrize("failure", ["broker_error", "enqueue_false", "enqueue_error", "bad_page"])
def test_organizer_failure_discards_exact_token_and_can_restart(
    store, tmp_path, monkeypatch, failure,
):
    from dataclasses import replace
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    for i in range(5):
        _put_epub(store, root, f"Acquisitions/error-{i}.epub",
                  metadata=META.replace("Roman commun", f"Roman error {i}"))
    _, plugin = _plugin(_service(store, root))
    host, tasks, renders = _host()
    c = plugin.controller(host, lambda *args: renders.append(args))
    before = rows(store)
    assert c.request("preview")
    _finish(tasks)
    old = c.page
    assert old.has_more
    original = plugin.broker.next_page
    submit = host.submit
    if failure == "broker_error":
        def fail(**_kw):
            raise RuntimeError("PRIVATE_ORGANIZER_EXCEPTION")
        monkeypatch.setattr(plugin.broker, "next_page", fail)
    elif failure == "bad_page":
        monkeypatch.setattr(plugin.broker, "next_page", lambda **_kw:
                            replace(old, mutation_authorized=True))
    elif failure == "enqueue_false":
        host.submit = lambda *_: False
    else:
        def reject(*_args):
            raise RuntimeError("PRIVATE_ORGANIZER_EXCEPTION")
        host.submit = reject
    admitted = c.request("next")
    if failure in ("enqueue_false", "enqueue_error"):
        assert not admitted
    else:
        assert admitted
        _finish(tasks)
    assert not c.busy and c.page is None
    # Restore the real broker before checking that the abandoned token
    # was actually revoked; a poisoned stub is not a valid safety witness.
    monkeypatch.setattr(plugin.broker, "next_page", original)
    host.submit = submit
    assert plugin.broker.next_page(token=old.token, confirmed=True) is None
    assert "PRIVATE_ORGANIZER_EXCEPTION" not in repr(renders)
    assert c.request("preview")
    _finish(tasks)
    assert c.page.page == 1 and rows(store) == before


def test_idle_organizer_cancel_revokes_preselected_keeper_advice(store, tmp_path):
    root, _ = setup_copies(store, tmp_path, (
        "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
    ))
    _, plugin = _plugin(_service(store, root))
    host, tasks, renders = _host()
    c = plugin.controller(host, lambda *args: renders.append(args))
    before = rows(store)
    assert c.request("preview")
    _finish(tasks)
    assert c.request("select")
    _finish(tasks)
    old = c.page
    assert old.selected == 1 and old.mutation_authorized is False
    assert c.cancel() and not c.cancel()
    assert c.page is None
    assert plugin.broker.select_all_advice(token=old.token, confirmed=True) is None
    assert c.request("preview")
    _finish(tasks)
    assert c.page.page == 1 and c.page.selected == 0
    assert rows(store) == before



@pytest.mark.parametrize("change", ["none", "sqlite", "bytes", "cancel"])
def test_guided_dry_run_has_two_explicit_steps_and_never_mutates(store, tmp_path, change):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    path, pid = _put_epub(store, root, "Acquisitions/old.epub")
    mod, plugin = _plugin(_service(store, root))
    host, tasks, renders = _host()
    c = plugin.controller(host, lambda *args: renders.append(args))
    original_bytes = path.read_bytes()
    assert not c.request("prepare") and not c.request("confirm")
    assert c.request("preview")
    _finish(tasks)
    first = c.page
    assert not c.request("confirm") and c.request("prepare")
    _finish(tasks)
    prepared = c.page
    assert prepared.dry_run_state == "prepared" and not prepared.mutation_authorized
    assert prepared.token != first.token
    assert not hasattr(prepared.dry_run_entries[0], "placement_id")
    assert "Plan à blanc" in mod.display(prepared)
    assert plugin.broker.confirm_dry_run(token=first.token, confirmed=True) is None
    if change == "sqlite":
        store.metadata_set("guided-dry-run-drift", "changed")
    elif change == "bytes":
        path.write_bytes(b"changed")
    elif change == "cancel":
        assert c.cancel() and not c.request("confirm")
    before = rows(store)
    if change != "cancel":
        assert c.request("confirm")
        _finish(tasks)
    if change == "none":
        assert c.page.dry_run_state == "confirmed" and not c.page.mutation_authorized
        assert "Simulation confirmée" in mod.display(c.page)
        assert not c.request("confirm") and not c.request("prepare")
        assert path.read_bytes() == original_bytes
    else:
        assert c.page is None
    assert plugin.broker.confirm_dry_run(token=prepared.token, confirmed=True) is None
    assert rows(store) == before
    assert path.exists() and not (path.parent / "Autrice - Roman commun.epub").exists()


def test_guided_simulation_selects_one_group_and_preserves_scope(store, tmp_path):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    for name, suffix in (("one", ""), ("two", '<dc:language xmlns:dc="http://purl.org/dc/elements/1.1/">fr</dc:language>')):
        _put_epub(store, root, f"Acquisitions/{name}.epub", metadata=META + suffix)
    module, plugin = _plugin(_service(store, root))
    host, tasks, renders = _host()
    controller = plugin.controller(host, lambda *args: renders.append(args))
    before = rows(store)
    assert controller.request("preview")
    _finish(tasks)
    first = controller.page
    assert len(first.groups) == 2
    assert not controller.request("prepare", group_index=True)
    assert not controller.request("prepare", group_index=2)
    assert controller.request("prepare", group_index=1)
    _finish(tasks)
    prepared = controller.page
    assert prepared.dry_run_group == 1
    assert all(e.current == first.groups[1].files[0].current
               for e in prepared.dry_run_entries)
    assert len(prepared.dry_run_entries) == 1
    assert prepared.dry_run_entries[0].state == "destination_conflict"
    assert "groupe 2 de cette page" in module.display(prepared)
    assert not controller.request("confirm", group_index=0)
    assert controller.request("confirm")
    _finish(tasks)
    assert controller.page.dry_run_group == 1
    assert controller.page.dry_run_state == "confirmed" and not controller.page.mutation_authorized
    assert controller.page.dry_run_entries == prepared.dry_run_entries
    assert plugin.broker.confirm_dry_run(token=prepared.token, confirmed=True) is None
    assert rows(store) == before


@pytest.mark.parametrize("safe_count", [0, 1])
def test_guided_rename_only_preserves_conflicts_and_can_reselect_empty(
    store, tmp_path, safe_count,
):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    _put_epub(store, root, "Acquisitions/one.epub")
    _put_epub(store, root, "Acquisitions/two.epub", metadata=META + '<dc:language xmlns:dc="http://purl.org/dc/elements/1.1/">fr</dc:language>')
    if safe_count:
        _put_epub(store, root, "Acquisitions/three.epub",
                  metadata=META.replace("Roman commun", "Roman unique"))
    if safe_count:
        with store._lock, store._connect() as db:
            db.execute("UPDATE catalog_entries SET title=? WHERE placement_id IN "
                       "(SELECT placement_id FROM placements WHERE path=?)",
                       ("Roman unique", str(root / "A/Acquisitions/three.epub")))
    module, plugin = _plugin(_service(store, root))
    host, tasks, renders = _host()
    c = plugin.controller(host, lambda *args: renders.append(args))
    before = rows(store)
    assert c.request("preview")
    _finish(tasks)
    original = c.page
    assert not c.request("prepare", group_index=0, rename_only=True)
    assert not c.request("prepare", rename_only=1)
    assert c.request("prepare", rename_only=True)
    _finish(tasks)
    prepared = c.page
    assert prepared.token != original.token and prepared.dry_run_rename_only
    assert prepared.dry_run_excluded == 2 and len(prepared.dry_run_entries) == safe_count
    assert not prepared.mutation_authorized
    assert "2 autre(s) avis non retenu(s)" in module.display(prepared)
    assert plugin.broker.confirm_dry_run(token=original.token, confirmed=True) is None
    if safe_count:
        assert prepared.dry_run_state == "prepared"
        assert c.request("confirm")
        _finish(tasks)
        assert c.page.dry_run_state == "confirmed" and c.page.dry_run_rename_only
        assert c.page.dry_run_entries == prepared.dry_run_entries
    else:
        assert prepared.dry_run_state == "empty"
        assert not c.request("confirm")
        assert plugin.broker.confirm_dry_run(token=prepared.token, confirmed=True) is None
        assert c.request("prepare")
        _finish(tasks)
        assert c.page.dry_run_state == "prepared" and not c.page.dry_run_rename_only
        assert all(e.state == "destination_conflict" for e in c.page.dry_run_entries)
    assert rows(store) == before and not c.page.mutation_authorized


@pytest.mark.parametrize("change", ["none", "bytes", "sqlite", "cancel"])
def test_revise_confirmed_plan_rotates_token_and_preserves_page(store, tmp_path, change):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    path, _ = _put_epub(store, root, "Acquisitions/old.epub")
    _, plugin = _plugin(_service(store, root))
    host, tasks, renders = _host()
    controller = plugin.controller(host, lambda *args: renders.append(args))
    assert controller.request("preview")
    _finish(tasks)
    original = controller.page
    assert controller.request("prepare", rename_only=True)
    _finish(tasks)
    assert controller.request("confirm")
    _finish(tasks)
    accepted = controller.page
    assert accepted.dry_run_state == "confirmed"
    assert plugin.broker.revise_dry_run(token=accepted.token) is None
    if change == "bytes":
        path.write_bytes(b"changed after confirmation")
    elif change == "sqlite":
        store.metadata_set("organizer-revision-drift", "changed")
    before, data = rows(store), path.read_bytes()
    assert controller.request("revise")
    if change == "cancel":
        assert controller.cancel()
    _finish(tasks)
    assert plugin.broker.confirm_dry_run(token=accepted.token, confirmed=True) is None
    assert plugin.broker.revise_dry_run(token=accepted.token, confirmed=True) is None
    if change == "none":
        revised = controller.page
        assert revised.token not in (original.token, accepted.token)
        assert revised.groups == original.groups and revised.page == original.page
        assert not revised.dry_run_state and not revised.dry_run_entries and not revised.selected
        assert controller.request("prepare", group_index=0)
        _finish(tasks)
        assert controller.page.dry_run_group == 0
        assert not controller.page.mutation_authorized
    else:
        assert controller.page is None
    assert rows(store) == before and path.read_bytes() == data
