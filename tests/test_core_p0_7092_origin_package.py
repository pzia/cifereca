"""#7092 package presentation and cancellation via public port."""
from pathlib import Path
import importlib.util
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.core.local_origin_review_broker import LocalOriginBatchReadBrokerImpl
from cifereca.core import local_origin_suggestion_batch as engine
from tests.test_core_p0_3171_atomic_publication_v14 import store, rows
from tests.test_core_p0_4030_suggestion_operator import _service
from tests.test_core_p0_4030_suggestion_batch import _extra

ROOT = Path(__file__).resolve().parents[1] / "src/plugins/maintenance-local-origin"


def rig(service):
    spec = importlib.util.spec_from_file_location("local_origin_package", ROOT / "cifereca_local_origin.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    broker = LocalOriginBatchReadBrokerImpl(service)
    plugin = mod.PLUGIN.activate(NS(broker=lambda k: broker if k == "library.origin.read" else None))
    tasks, renders = [], []
    host = NS(identity_id="A", live=True)
    host.is_current = lambda: host.live
    def submit(label, work, done):
        tasks.append((work, done))
        return True
    host.submit = submit
    controller = plugin.controller(host, lambda *args: renders.append(args))
    return mod, plugin, host, controller, tasks, renders


def test_package_paginates_only_on_explicit_click(store, tmp_path):
    descriptor, = json.loads((ROOT / "cifereca-plugin.json").read_text())["plugins"]
    assert descriptor["requires_brokers"] == ["library.origin.read"]
    assert descriptor["enabled_by_default"] is True
    service, root, _ = _service(store, tmp_path)
    for i in range(4):
        _extra(store, root, f"book-{i}.pdf")
    mod, plugin, host, controller, tasks, renders = rig(service)
    before = rows(store)
    assert not tasks and not controller.request(restart=False)
    assert controller.request(restart=True) and not controller.request(restart=True)
    work, done = tasks[-1]
    value = work()
    done(value)
    done(value)
    assert controller.page.page == 1 and controller.page.can_continue
    assert "absence non prouvée" in mod.display(controller.page)
    assert controller.request(restart=False)
    work, done = tasks[-1]
    done(work())
    assert controller.page.page == 2 and not controller.page.can_continue
    assert len(renders) == 4 and rows(store) == before


@pytest.mark.parametrize("change", ["cancel", "dispose", "disable", "route", "identity"])
@pytest.mark.parametrize("phase", ["before_worker", "during_worker", "after_worker"])
def test_revocation_or_cancellation_discards_result(store, tmp_path, monkeypatch, change, phase):
    service, root, _ = _service(store, tmp_path)
    _extra(store, root, "second.pdf")
    mod, plugin, host, controller, tasks, renders = rig(service)
    before = rows(store)
    calls = []
    actual = engine.suggest_local_placement_documents

    def revoke():
        if change == "cancel":
            assert controller.cancel()
        elif change == "dispose":
            controller.dispose()
        elif change == "disable":
            plugin.deactivate()
        elif change == "route":
            host.live = False
        else:
            host.identity_id = "B"

    def observed(*args, **kwargs):
        result = actual(*args, **kwargs)
        calls.append(True)
        if phase == "during_worker" and len(calls) == 1:
            revoke()
        return result

    monkeypatch.setattr(engine, "suggest_local_placement_documents", observed)
    assert controller.request(restart=True)
    work, done = tasks[0]
    if phase == "before_worker":
        revoke()
    value = work()
    if phase == "after_worker":
        assert value is not None
        revoke()
    done(value)
    done(value)
    assert not any(page is not None for page, _ in renders)
    assert controller.page is None and rows(store) == before
    assert len(calls) == {"before_worker": 0, "during_worker": 1, "after_worker": 2}[phase]
    if change != "cancel":
        assert not controller.request(restart=True)


def test_budget_skip_is_not_negative_match(store, tmp_path, monkeypatch):
    service, root, _ = _service(store, tmp_path)
    mod, _, _, controller, tasks, _ = rig(service)
    monkeypatch.setattr(engine, "_MAX_BYTES", 1)
    assert controller.request(restart=True)
    work, done = tasks[-1]
    done(work())
    assert controller.page.entries[0].known_matches is None
    assert "Non évalué" in mod.display(controller.page)
