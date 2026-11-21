"""#7100 actual packages over actual read ports and activation revocation.

The register here is isolated; no desktop route or production provider is
consulted. Reuse the synthetic SQLite and filesystem receipts from each lot.
"""
from pathlib import Path

from cifereca.core.maintenance_read_ports import maintenance_read_ports
from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.plugin_api.library_organizer import LibraryOrganizerReadBroker
from cifereca.plugin_api.local_origin_review import LocalOriginBatchReadBroker
from cifereca.plugin_api.derived_maintenance import DerivedMaintenanceReadBroker
from tests.test_core_p0_7100_installed_maintenance_packages import TOOLS, archive
from tests.test_core_p0_3171_atomic_publication_v14 import store, rows
from tests.test_core_p0_3176_organizer_review import _service as organizer_service
from tests.test_p1_4496_keeper_review import setup_copies
from tests.test_core_p0_4030_suggestion_operator import _service as origin_service
from tests.test_core_p0_4030_suggestion_batch import _extra
from tests.test_core_p0_4520_recovery_operator import _setup


def runtime(tmp_path, service, spec, protocol):
    factories = maintenance_read_ports(service)
    assert set(factories) == {item[3] for item in TOOLS} | {"library.origin.detail.read"}
    manager = PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "state.json"),
        brokers=factories,
    )
    manager.install(archive(tmp_path, spec))
    lease = manager.service(spec[2]).broker
    assert isinstance(lease, protocol)
    assert not hasattr(lease, "revoke") and not hasattr(lease, "storage")
    return manager, lease


def reload(manager, spec, old):
    key = spec[2]
    manager.set_enabled(key, False)
    assert manager.service(key) is None
    manager.set_enabled(key, True)
    fresh = manager.service(key).broker
    assert fresh is not old
    return fresh


def test_organizer_activation_never_reuses_old_keeper_advice(store, tmp_path):
    spec = TOOLS[0]
    root, _ = setup_copies(store, tmp_path, (
        "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
    ))
    service = organizer_service(store, root)
    before = rows(store)
    manager, old = runtime(tmp_path, service, spec, LibraryOrganizerReadBroker)
    try:
        page = old.preview(identity_id="A", confirmed=True)
        assert page.eligible == 1 and not page.mutation_authorized
        fresh = reload(manager, spec, old)
        assert old.preview(identity_id="A", confirmed=True) is None
        assert old.select_all_advice(token=page.token, confirmed=True) is None
        assert fresh.select_all_advice(token=page.token, confirmed=True) is None
        new = fresh.preview(identity_id="A", confirmed=True)
        selected = fresh.select_all_advice(token=new.token, confirmed=True)
        assert selected.selected == 1 and not selected.mutation_authorized
        assert rows(store) == before
    finally:
        manager.close()


def test_origin_activation_never_reuses_old_continuation(store, tmp_path):
    spec = TOOLS[1]
    service, root, _ = origin_service(store, tmp_path)
    for i in range(4):
        _extra(store, root, f"more-{i}.pdf")
    before = rows(store)
    manager, old = runtime(tmp_path, service, spec, LocalOriginBatchReadBroker)
    try:
        page = old.preview(identity_id="A", confirmed=True)
        assert page.can_continue
        fresh = reload(manager, spec, old)
        assert old.next_page(token=page.token, confirmed=True) is None
        assert fresh.next_page(token=page.token, confirmed=True) is None
        new = fresh.preview(identity_id="A", confirmed=True)
        second = fresh.next_page(token=new.token, confirmed=True)
        assert second.page == 2 and not second.provider_access_performed
        assert rows(store) == before
    finally:
        manager.close()


def test_derived_activation_never_reuses_old_simulation(tmp_path):
    spec = TOOLS[2]
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    manager = None
    try:
        before = db.metadata_get(reservation.key)
        manager, old = runtime(tmp_path, service, spec, DerivedMaintenanceReadBroker)
        page = old.list_receipts(identity_id="id1", confirmed=True)
        token = old.verify(token=page.choices[0].token, confirmed=True)
        assert token
        fresh = reload(manager, spec, old)
        assert old.simulate(token=token, confirmed=True) == "review_required"
        assert fresh.simulate(token=token, confirmed=True) == "review_required"
        assert not hasattr(fresh, "recover") and not hasattr(fresh, "compact")
        new = fresh.list_receipts(identity_id="id1", confirmed=True)
        token = fresh.verify(token=new.choices[0].token, confirmed=True)
        assert fresh.simulate(token=token, confirmed=True) == "verified_no_write"
        assert db.metadata_get(reservation.key) == before and len(db.placements()) == 1
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        if manager is not None:
            manager.close()
        db.close()


def test_installed_contextual_contribution_uses_same_port_and_is_optional(store, tmp_path):
    from types import SimpleNamespace as NS
    from cifereca.gui.guided_book_review_host import (
        mount_guided_book_reviews, dispose_guided_book_reviews,
    )
    from cifereca.plugin_api.guided_book_review import GuidedBookReviewTool
    from tests.test_coreui_p0_4030_suggestion_action import _host
    from tests.test_core_p0_4030_suggestion_operator import _more_public
    window, panel, row, item, token, tasks, buttons, groups, toasts = _host(store, tmp_path)
    _more_public(store, 21)
    window._identity_context_generation = 1
    spec = TOOLS[1]
    manager, _ = runtime(tmp_path, window.service, spec, LocalOriginBatchReadBroker)
    window.plugin_manager = manager
    before = rows(store)
    try:
        assert isinstance(manager.service(spec[2]), GuidedBookReviewTool)
        first, = mount_guided_book_reviews(window, panel, row, item, token=token)
        window._guided_book_review_mounts = (first,)
        button, = buttons
        assert not tasks and not groups
        button.callback(button)
        work, done, _ = tasks[0]
        done(work())
        assert len(groups) == 1 and ("Page", "1") in groups[0][1]
        button.callback(button)
        work, done, _ = tasks[1]
        result = work()
        assert result.page == 2
        manager.set_enabled(spec[2], False)
        dispose_guided_book_reviews(window)
        done(result)
        button.callback(button)
        assert len(tasks) == 2 and len(groups) == 1
        assert mount_guided_book_reviews(window, panel, row, item, token=token) == ()
        manager.set_enabled(spec[2], True)
        buttons.clear()
        fresh, = mount_guided_book_reviews(window, panel, row, item, token=token)
        try:
            assert fresh is not first
            button, = buttons
            button.callback(button)
            work, done, _ = tasks[-1]
            done(work())
            assert ("Page", "1") in groups[-1][1]
        finally:
            fresh.dispose()
        assert rows(store) == before
    finally:
        dispose_guided_book_reviews(window)
        manager.close()
