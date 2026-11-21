"""Real contextual port behind the plugin's widget-independent contribution."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.local_origin_detail_broker import LocalOriginDetailReadFactory
from tests.test_core_p0_4030_public_catalogue import rig, store, rows
from tests.test_core_p0_4030_suggestion_operator import _more_public

ROOT = Path(__file__).resolve().parents[1] / "src/plugins/maintenance-local-origin"


def mounted(rig):
    service, session, request, calls, path = rig
    spec = importlib.util.spec_from_file_location("origin_detail", ROOT / "cifereca_local_origin_detail.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    active, tasks, actions, groups, toasts = [True], [], {}, [], []
    host = NS(identity_id=request.identity_id, placement_id=request.placement_id, live=True)
    host.is_current = lambda: host.live
    host.submit = lambda label, work, done: (tasks.append((work, done)) or True)
    host.add_action = lambda key, title, tooltip, action: actions.update({key: (title, tooltip, action)})
    host.add_group = lambda *args: groups.append(args)
    host.toast = toasts.append
    broker = LocalOriginDetailReadFactory(service).for_activation("fixture", current=lambda: active[0])
    controller = mod.DetailController(broker, host, lambda: active[0])
    dispose = controller.mount()
    return controller, dispose, host, active, tasks, actions, groups, toasts


def deliver(tasks):
    work, done = tasks[-1]
    value = work()
    done(value)
    done(value)


def test_mount_is_inert_and_public_action_is_explicit_one_shot(rig):
    service, session, request, calls, path = rig
    before, data = rows(service.storage), path.read_bytes()
    c, dispose, host, active, tasks, actions, groups, toasts = mounted(rig)
    assert set(actions) == {"preview"} and not tasks and not calls
    assert actions["preview"][2]() is True
    assert actions["preview"][2]() is False
    deliver(tasks)
    assert len(groups) == 1 and not calls and not toasts
    title, tooltip, search = actions["public-0"]
    assert title == "Envoyer le titre à new-catalogue" and "seul le titre OPF" in tooltip
    assert search() is True and search() is False
    deliver(tasks)
    assert search() is False and len(calls) == 1
    assert len(groups) == 2 and "aucune origine ni droit" in str(groups[1]).lower()
    assert rows(service.storage) == before and path.read_bytes() == data


@pytest.mark.parametrize("change", ["view", "activation", "placement", "dispose"])
@pytest.mark.parametrize("phase", ["before_worker", "after_worker"])
def test_revoked_view_never_renders_and_queued_work_never_dispatches(rig, change, phase):
    service, session, request, calls, path = rig
    c, dispose, host, active, tasks, actions, groups, toasts = mounted(rig)
    actions["preview"][2]()
    deliver(tasks)
    search = actions["public-0"][2]
    assert search()
    work, done = tasks[-1]

    def revoke():
        if change == "view":
            host.live = False
        elif change == "activation":
            active[0] = False
        elif change == "placement":
            host.placement_id = "other"
        else:
            dispose()

    if phase == "before_worker":
        revoke()
    result = work()
    if phase == "after_worker":
        revoke()
    done(result)
    assert len(groups) == 1 and not toasts
    assert len(calls) == (phase == "after_worker")
    assert search() is False
