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
