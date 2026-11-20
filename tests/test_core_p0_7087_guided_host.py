"""#7087 generic guided host does not expose window or execute stale jobs."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.guided_maintenance_host import admit_guided_maintenance_host
from cifereca.plugin_api import ResolvedPluginService


def rig():
    tasks = []
    plugin = object()
    owner = NS(identity_id="A")
    manager = NS(_registry_generation=3, live=plugin)
    manager.service = lambda key: manager.live if key == "fixture.tool" else None
    runner = NS(submit=lambda *args, **kwargs: tasks.append((args, kwargs)))
    window = NS(service=NS(current_identity=owner, _source_context_epoch=1),
                runner=runner, plugin_manager=manager, _closed=False,
                _route_display_token=object(), _identity_context_generation=2)
    resolved = ResolvedPluginService(
        "fixture.tool", "1", "fixture", "1", "maintenance.tool", plugin,
        registry_generation=3,
    )
    lease = admit_guided_maintenance_host(window, resolved)
    assert lease is not None and lease.identity_id == "A"
    return window, resolved, lease, tasks


def test_guided_host_queues_only_explicit_work_and_delivers_once():
    window, resolved, lease, tasks = rig()
    calls, values = [], []
    assert not tasks and lease.is_current()
    assert not hasattr(lease, "service") and not hasattr(lease, "window")
    assert lease.submit("private label", lambda: calls.append(1) or 42, values.append)
    assert not lease.submit("duplicate", lambda: pytest.fail("duplicate"), values.append)
    assert not calls and not values
    args, options = tasks[0]
    assert args[0] == "Revue locale de maintenance…" and options["origin"] == "user"
    assert options["kind"] == "maintenance-plan"
    result = args[1]()
    args[2](result)
    args[3](RuntimeError("private error"))
    args[2](result)
    assert calls == [1] and values == [42]
    assert lease.submit("second", lambda: 43, values.append)
    args, _ = tasks[1]
    args[3](RuntimeError("private error"))
    assert values == [42, None]


@pytest.mark.parametrize("change", ["dispose", "route", "identity", "owner", "epoch",
                                     "registry", "plugin", "close", "service"])
@pytest.mark.parametrize("phase", ["before_worker", "after_worker"])
def test_revocation_blocks_work_or_late_delivery(change, phase):
    window, resolved, lease, tasks = rig()
    calls, values = [], []
    assert lease.submit("read", lambda: calls.append(True) or "private result", values.append)
    args, _ = tasks[0]
    if phase == "after_worker":
        result = args[1]()
        assert result == "private result"
    if change == "dispose":
        lease.dispose()
    elif change == "route":
        window._route_display_token = object()
    elif change == "identity":
        window._identity_context_generation += 1
    elif change == "owner":
        window.service.current_identity = NS(identity_id="A")
    elif change == "epoch":
        window.service._source_context_epoch += 1
    elif change == "registry":
        window.plugin_manager._registry_generation += 1
    elif change == "plugin":
        window.plugin_manager.live = object()
    elif change == "close":
        window._closed = True
    else:
        window.service = NS(**vars(window.service))
    if phase == "before_worker":
        result = args[1]()
        assert result is None
        assert not calls
    args[2](result)
    assert not values and not lease.is_current()
    assert not lease.submit("stale", lambda: pytest.fail("stale"), values.append)
    assert len(tasks) == 1


def test_enqueue_failure_releases_ticket_without_raw_error():
    window, _, lease, _ = rig()
    def fail(*args, **kwargs):
        raise RuntimeError("private exception")
    window.runner.submit = fail
    assert not lease.submit("x", lambda: 1, lambda v: pytest.fail("not admitted"))
    tasks = []
    window.runner.submit = lambda *args, **kwargs: tasks.append(args)
    assert lease.submit("retry", lambda: 2, lambda v: None)
    assert len(tasks) == 1
