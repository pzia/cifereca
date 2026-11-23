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
    assert descriptor["requires_brokers"] == ["library.origin.read", "library.origin.detail.read"]
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


@pytest.mark.parametrize("phase", ["late_first", "queued_next", "idle_dispose"])
def test_cancel_or_detach_revokes_exact_continuation_token(store, tmp_path, phase):
    service, root, _ = _service(store, tmp_path)
    for i in range(5):
        _extra(store, root, f"cancel-token-{i}.pdf")
    _, _, _, c, tasks, renders = rig(service)
    before = rows(store)
    assert c.request(restart=True)
    work, done = tasks[-1]
    page = work()
    assert page.can_continue
    if phase == "late_first":
        assert c.cancel()
        done(page)
        assert c.page is None
    else:
        done(page)
        if phase == "idle_dispose":
            c.dispose()
        else:
            assert c.request(restart=False)
            work, done = tasks[-1]
            assert c.cancel()
            assert work() is None
            done(None)
    assert c.broker.next_page(token=page.token, confirmed=True) is None
    if phase != "idle_dispose":
        assert c.request(restart=True)
        work, done = tasks[-1]
        fresh = work()
        done(fresh)
        assert c.page is fresh and fresh.page == 1 and fresh.can_continue
    assert rows(store) == before


@pytest.mark.parametrize("failure", ["worker_exception", "enqueue_refused", "enqueue_exception", "invalid_result"])
def test_failed_next_page_revokes_previous_token_and_recovers(store, tmp_path, monkeypatch, failure):
    from dataclasses import replace
    service, root, _ = _service(store, tmp_path)
    for i in range(5):
        _extra(store, root, f"error-token-{i}.pdf")
    _, _, host, c, tasks, renders = rig(service)
    before = rows(store)
    assert c.request(restart=True)
    work, done = tasks[-1]
    page = work()
    done(page)
    assert page.can_continue
    submit = host.submit
    next_page = c.broker.next_page

    def fail(**kwargs):
        raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")

    if failure == "worker_exception":
        monkeypatch.setattr(c.broker, "next_page", fail)
    elif failure == "enqueue_refused":
        host.submit = lambda *_: False
    elif failure == "enqueue_exception":
        def reject(*args):
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")
        host.submit = reject
    else:
        monkeypatch.setattr(c.broker, "next_page", lambda **kw: replace(page, coverage_complete=True))
    admitted = c.request(restart=False)
    if failure in ("enqueue_refused", "enqueue_exception"):
        assert not admitted
    else:
        assert admitted
        work, done = tasks[-1]
        done(work())
    assert c.page is None and c.stop is None and renders[-1] == (None, "ready")
    assert "SYNTHETIC_PRIVATE_ERROR" not in repr(renders)
    monkeypatch.setattr(c.broker, "next_page", next_page)
    host.submit = submit
    assert c.broker.next_page(token=page.token, confirmed=True) is None
    assert c.request(restart=True)
    work, done = tasks[-1]
    fresh = work()
    done(fresh)
    assert fresh.page == 1 and c.page is fresh and fresh.can_continue
    assert rows(store) == before



def test_explicit_declaration_filter_is_read_only_and_restart_scoped(store, tmp_path):
    from tests.test_core_p0_4030_scoped_documentary_suggestions import _record_declaration
    service, root, pid = _service(store, tmp_path)
    path = Path(store.placement_get(pid)["path"])
    before_bytes = path.read_bytes()
    _record_declaration(store, path)
    for i in range(5):
        _extra(store, root, f"for-filter-{i}.pdf")
    mod, plugin, host, c, tasks, renders = rig(service)
    before = rows(store)
    assert not c.request(restart=True, include_declared=1)
    assert c.request(restart=True)
    work, done = tasks[-1]
    done(work())
    first = c.page
    assert first.can_continue
    default_text = mod.display(first)
    assert not c.request(restart=False, include_declared=True)
    assert c.request(restart=False)
    work, done = tasks[-1]
    done(work())
    default_text += mod.display(c.page)
    assert "origine déclarée, non attestée" in default_text
    assert c.request(restart=True, include_declared=True)
    work, done = tasks[-1]
    done(work())
    selected_text = mod.display(c.page)
    assert c.page.can_continue
    assert c.request(restart=False)
    work, done = tasks[-1]
    done(work())
    selected_text += mod.display(c.page)
    assert "déclaration conservée" in selected_text
    assert "origine déclarée, non attestée" not in selected_text
    assert c.broker.next_page(token=first.token, confirmed=True) is None
    assert rows(store) == before and path.read_bytes() == before_bytes


@pytest.mark.parametrize("include_declared", [False, True])
def test_restart_revokes_previous_page_before_queued_worker(store, tmp_path, include_declared):
    service, root, _ = _service(store, tmp_path)
    for i in range(5):
        _extra(store, root, f"restart-token-{i}.pdf")
    _, _, _, controller, tasks, _ = rig(service)
    before = rows(store)
    assert controller.request(restart=True)
    work, done = tasks[-1]
    old = work()
    done(old)
    assert old.can_continue
    assert controller.request(restart=True, include_declared=include_declared)
    # The new worker has not run: the old cursor must already be revoked.
    assert controller.broker.next_page(token=old.token, confirmed=True) is None
    work, done = tasks[-1]
    fresh = work()
    done(fresh)
    assert controller.page is fresh and fresh.page == 1 and fresh.can_continue
    assert fresh.token != old.token
    assert rows(store) == before

def test_idle_batch_cancel_revokes_exact_page_before_next_worker(store, tmp_path):
    service, root, _ = _service(store, tmp_path)
    for index in range(5):
        _extra(store, root, f"idle-cancel-{index}.pdf")
    _, _, host, controller, tasks, renders = rig(service)
    before = rows(store)
    assert controller.request(restart=True)
    work, done = tasks[-1]
    old = work()
    done(old)
    assert old.can_continue and controller.page is old
    assert controller.cancel() and not controller.cancel()
    assert controller.page is None and controller.stop is None
    assert renders[-1] == (None, "cancelled")
    assert controller.broker.next_page(token=old.token, confirmed=True) is None
    assert not controller.request(restart=False)
    assert controller.request(restart=True)
    refreshed_work, refreshed_done = tasks[-1]
    refreshed = refreshed_work()
    refreshed_done(refreshed)
    assert refreshed is controller.page and refreshed.page == 1
    assert refreshed.token != old.token and rows(store) == before


def test_idle_cancel_does_not_affect_another_mounted_batch_view(store, tmp_path):
    service, root, _ = _service(store, tmp_path)
    for index in range(5):
        _extra(store, root, f"two-views-{index}.pdf")
    _, _, _, first, tasks_a, renders_a = rig(service)
    _, _, _, other, tasks_b, renders_b = rig(service)
    assert first.request(restart=True)
    work_a, done_a = tasks_a[-1]
    page_a = work_a()
    done_a(page_a)
    assert first.cancel()
    assert renders_a[-1] == (None, "cancelled")
    assert other.request(restart=True)
    work_b, done_b = tasks_b[-1]
    page_b = work_b()
    done_b(page_b)
    assert other.page is page_b and page_b.page == 1
    assert page_b.token != page_a.token
    assert other.cancel()
