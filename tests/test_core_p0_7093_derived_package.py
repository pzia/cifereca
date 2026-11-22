"""#7093: derived maintenance contribution uses public observations only."""
from pathlib import Path
import importlib.util
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.core.derived_maintenance_broker import DerivedMaintenanceReadBrokerImpl
from tests.test_core_p0_4520_recovery_operator import _setup

ROOT = Path(__file__).resolve().parents[1] / "src/plugins/maintenance-derived-variants"


def rig(service, *, capacity=False):
    spec = importlib.util.spec_from_file_location("derived_package", ROOT / "cifereca_derived_maintenance.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    broker = DerivedMaintenanceReadBrokerImpl(service)
    plugin = (mod.CAPACITY_PLUGIN if capacity else mod.PLUGIN).activate(NS(broker=lambda key: broker if key == "library.derived.read" else None))
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
    recovery, capacity = json.loads((ROOT / "cifereca-plugin.json").read_text())["plugins"]
    assert all(d["requires_brokers"] == ["library.derived.read"] and d["enabled_by_default"] is True
               for d in (recovery, capacity))
    assert "maintenance.group.recovery" in recovery["capabilities"]
    assert "maintenance.group.storage" in capacity["capabilities"]
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        mod, plugin, host, c, tasks, renders = rig(service)
        before = db.metadata_get(reservation.key)
        assert not tasks and not c.request("simulate", confirmed=True)
        assert c.request("overview") and not c.request("capacity")
        assert "1 déjà liés" in mod.overview_text(finish(tasks))
        assert not c.request("capacity")
        _, _, capacity_host, capacity_c, capacity_tasks, capacity_renders = rig(service, capacity=True)
        assert not capacity_c.request("list") and not capacity_c.request("overview")
        assert capacity_c.request("capacity")
        description = mod.capacity_text(finish(capacity_tasks))
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
        assert len(tasks) == 4 and len(renders) == 8
        assert len(capacity_tasks) == 1 and len(capacity_renders) == 2
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


@pytest.mark.parametrize("case", ["paired", "orphan", "corrupt"])
def test_capacity_explains_compact_admission_refusal(tmp_path, case):
    from cifereca.core import derived_variant_compact_receipt as compact
    from cifereca.core import derived_variant_journal as journal
    from tests.test_core_p0_4520_derived_journal import _publish_reserved
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        key = journal._COMMITTED + reservation.receipt_sha256
        raw = db.metadata_get(key)
        pk, proof = compact.encode_committed_witness(key, raw)
        db.metadata_set(pk, proof if case != "corrupt" else "unreadable")
        if case == "orphan":
            with db._lock, db._connect() as sql:
                sql.execute("DELETE FROM metadata WHERE key=?", (key,))
        mod, plugin, host, controller, tasks, renders = rig(service, capacity=True)
        assert controller.request("capacity")
        description = mod.capacity_text(finish(tasks))
        assert "Traces compactes" in description
        assert ("Nouvelles réservations refusées" in description) is (case != "paired")
        if case == "orphan":
            assert "reçu original non vérifié" in description
        assert "Aucun quota libéré" in description
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


@pytest.mark.parametrize("action", ["list", "verify", "simulate"])
@pytest.mark.parametrize("phase", ["before_worker", "after_worker"])
def test_cancel_discards_queued_or_late_review_and_revokes_exact_token(tmp_path, action, phase):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        _, _, _, c, tasks, renders = rig(service)
        before = db.metadata_get(reservation.key)
        old_page = None
        if action != "list":
            assert c.request("list")
            old_page = finish(tasks)
        if action == "simulate":
            assert c.request("verify", choice=old_page.choices[0])
            finish(tasks)
        if action == "verify":
            assert c.request(action, choice=old_page.choices[0])
        else:
            assert c.request(action, confirmed=True)
        work, done = tasks[-1]
        if phase == "after_worker":
            value = work()
            assert value is not None
        assert c.cancel() and not c.cancel() and not c.request("list")
        assert renders[-1][1] == "cancelling"
        if phase == "before_worker":
            value = work()
            assert value is None
        done(value)
        done(value)
        assert renders[-1][1:] == ("cancelled", None)
        assert not c.busy and c.page is None and c.reviewed is None
        assert not c.request("simulate", confirmed=True)
        if old_page is not None:
            assert c.broker.verify(token=old_page.choices[0].token, confirmed=True) is None
        if action == "list" and value is not None:
            assert c.broker.verify(token=value.choices[0].token, confirmed=True) is None
        if action == "verify" and value is not None:
            assert c.broker.simulate(token=value, confirmed=True) == "review_required"
        assert c.request("list")
        fresh = finish(tasks)
        assert fresh is not None and c.page is fresh
        assert db.metadata_get(reservation.key) == before and len(db.placements()) == 1
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


def test_cancel_idle_review_and_error_recover_without_mutation(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        _, _, _, c, tasks, renders = rig(service)
        assert c.request("list")
        page = finish(tasks)
        assert c.request("verify", choice=page.choices[0])
        token = finish(tasks)
        assert c.cancel() and not c.cancel()
        assert c.broker.simulate(token=token, confirmed=True) == "review_required"
        assert renders[-1][1] == "cancelled"

        def fail(**kwargs):
            raise RuntimeError("SYNTHETIC_PRIVATE_EXCEPTION")

        monkeypatch.setattr(c.broker, "list_receipts", fail)
        assert c.request("list") and finish(tasks) is None
        assert renders[-1] == ("list", "ready", None)
        assert not c.busy and c.page is None and c.reviewed is None
        assert "SYNTHETIC_PRIVATE_EXCEPTION" not in repr(renders)
        assert db.metadata_get(reservation.key) is not None
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


@pytest.mark.parametrize("phase", ["before_worker", "after_worker"])
def test_capacity_cancellation_is_independent_of_recovery(tmp_path, phase):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        _, _, _, recovery, rtasks, _ = rig(service)
        mod, _, _, capacity, ctasks, renders = rig(service, capacity=True)
        assert recovery.request("list")
        page = finish(rtasks)
        before = db.metadata_get(reservation.key)
        assert capacity.request("capacity")
        work, done = ctasks[-1]
        if phase == "after_worker":
            value = work()
            assert value is not None
        assert capacity.cancel()
        if phase == "before_worker":
            value = work()
            assert value is None
        done(value)
        assert renders[-1] == ("capacity", "cancelled", None)
        assert recovery.request("verify", choice=page.choices[0])
        assert finish(rtasks) is not None
        assert db.metadata_get(reservation.key) == before
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()
