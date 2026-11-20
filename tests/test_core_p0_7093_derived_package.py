"""#7093: derived maintenance contribution uses public observations only."""
from pathlib import Path
import importlib.util
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.core.derived_maintenance_broker import DerivedMaintenanceReadBrokerImpl
from tests.test_core_p0_4520_recovery_operator import _setup

ROOT = Path(__file__).resolve().parents[1] / "src/plugins/maintenance-derived-variants"


def rig(service):
    spec = importlib.util.spec_from_file_location("derived_package", ROOT / "cifereca_derived_maintenance.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    broker = DerivedMaintenanceReadBrokerImpl(service)
    plugin = mod.PLUGIN.activate(NS(broker=lambda key: broker if key == "library.derived.read" else None))
    host = NS(identity_id="id1", live=True)
    host.is_current = lambda: host.live
    tasks, renders = [], []
    def submit(label, work, done):
        tasks.append((work, done))
        return True
    host.submit = submit
    controller = plugin.controller(host, lambda _c, action, state, value: renders.append((action, state, value)))
    return mod, plugin, host, controller, tasks, renders


def finish(tasks):
    work, done = tasks[-1]
    value = work()
    done(value)
    done(value)
    return value


def test_manifest_capacity_overview_and_separate_confirmation(tmp_path):
    descriptor, = json.loads((ROOT / "cifereca-plugin.json").read_text())["plugins"]
    assert descriptor["requires_brokers"] == ["library.derived.read"]
    assert descriptor["enabled_by_default"] is True
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        mod, plugin, host, c, tasks, renders = rig(service)
        before = db.metadata_get(reservation.key)
        assert not tasks and not c.request("simulate", confirmed=True)
        assert c.request("overview") and not c.request("capacity")
        assert "1 déjà liés" in mod.overview_text(finish(tasks))
        assert c.request("capacity")
        description = mod.capacity_text(finish(tasks))
        assert "Stock nommé" in description and "Traces compactes" in description
        assert "Aucun quota libéré" in description
        assert c.request("list")
        finish(tasks)
        choice, = c.page.choices
        assert c.request("verify", choice=choice)
        finish(tasks)
        assert c.reviewed is not None and not c.request("simulate")
        assert c.request("simulate", confirmed=True)
        assert finish(tasks) == "verified_no_write"
        assert not c.request("simulate", confirmed=True)
        assert len(tasks) == 5 and len(renders) == 10
        assert db.metadata_get(reservation.key) == before and len(db.placements()) == 1
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


@pytest.mark.parametrize("change", ["dispose", "disable", "route", "identity"])
@pytest.mark.parametrize("phase", ["before_worker", "after_worker"])
def test_revoked_simulation_never_renders(tmp_path, change, phase):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        mod, plugin, host, c, tasks, renders = rig(service)
        before = db.metadata_get(reservation.key)
        assert c.request("list")
        finish(tasks)
        assert c.request("verify", choice=c.page.choices[0])
        finish(tasks)
        assert c.request("simulate", confirmed=True)
        work, done = tasks[-1]
        if phase == "after_worker":
            value = work()
            assert value == "verified_no_write"
        if change == "dispose":
            c.dispose()
        elif change == "disable":
            plugin.deactivate()
        elif change == "route":
            host.live = False
        else:
            host.identity_id = "other"
        if phase == "before_worker":
            value = work()
            assert value is None
        done(value)
        done(value)
        assert len(renders) == 5 and not c.request("list")
        assert db.metadata_get(reservation.key) == before and len(db.placements()) == 1
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


def test_changed_file_requires_fresh_review(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        _, _, _, c, tasks, _ = rig(service)
        assert c.request("list")
        finish(tasks)
        assert c.request("verify", choice=c.page.choices[0])
        finish(tasks)
        Path(intent.candidate_path).unlink()
        assert c.request("simulate", confirmed=True)
        assert finish(tasks) == "review_required"
        assert Path(intent.staged_path).exists()
        assert not c.request("simulate", confirmed=True)
        assert db.metadata_get(reservation.key) is not None
    finally:
        db.close()
