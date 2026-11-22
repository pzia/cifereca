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


def _legacy_groups(groups):
    """Preserve legacy action assertions while allowing the read-only rank overlay."""
    return [group for group in groups
            if group[0] != "Classement documentaire — lecture seule"]


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
    assert len(_legacy_groups(groups)) == 2 and "aucune origine ni droit" in str(_legacy_groups(groups)[1]).lower()
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


@pytest.mark.parametrize("public_count,private_count", [(0,16),(8,16),(3,2),(8,0)])
def test_choice_window_respects_actual_host_action_budget(rig, public_count, private_count):
    from dataclasses import replace
    from cifereca.plugin_api.local_origin_detail import (
        LocalOriginPrivateChoice, LocalOriginPublicChoice,
    )
    service, session, request, calls, path = rig
    c, dispose, host, active, tasks, actions, groups, toasts = mounted(rig)
    page = c.broker.preview(identity_id=request.identity_id, placement_id=request.placement_id,
                            still_current=lambda: True, confirmed=True)
    public = tuple(LocalOriginPublicChoice(f"pub-{i}", f"catalogue-{i}") for i in range(public_count))
    private = tuple(LocalOriginPrivateChoice(
        f"priv-{i}", "provider-" + "p"*120,
        "credential-" + "c"*220, f"account-{i}-" + "a"*200,
        "purchase" if i < 8 else "loan",
    ) for i in range(private_count))
    c.broker = NS(preview=lambda **kw: replace(page, public_choices=public, private_choices=private))
    original = host.add_action
    def limited(key, title, tooltip, action):
        assert len(actions) < 12 and len(title) <= 256 and len(tooltip) <= 1024
        original(key, title, tooltip, action)
    host.add_action = limited
    assert actions["preview"][2]()
    deliver(tasks)
    remaining = min(private_count, 11-public_count)
    assert len(actions) == 1 + public_count + remaining
    assert len(groups) == (2 if private_count else 1)
    if private_count:
        text = repr(groups[-1])
        for index in range(remaining):
            assert private[index].account_id in text
            assert private[index].account_id in actions[f"private-{index}"][1]
        if private_count > remaining:
            assert f"{private_count-remaining} choix supplémentaires" in text
    assert not calls


def test_paged_host_exposes_all_bounded_choices_without_network(rig):
    from dataclasses import replace
    from cifereca.plugin_api.local_origin_detail import (
        LocalOriginPrivateChoice, LocalOriginPublicChoice,
    )
    service, session, request, calls, path = rig
    c, dispose, host, active, tasks, actions, groups, toasts = mounted(rig)
    page = c.broker.preview(identity_id=request.identity_id, placement_id=request.placement_id,
                            still_current=lambda: True, confirmed=True)
    public = tuple(LocalOriginPublicChoice(f"pub-{i}", f"catalogue-{i}") for i in range(8))
    private = tuple(LocalOriginPrivateChoice(f"priv-{i}", "provider", "credential",
                                          f"account-{i}", "purchase") for i in range(16))
    c.broker = NS(preview=lambda **kw: replace(page, public_choices=public, private_choices=private))
    host.retire_action = lambda key: actions.pop(key, None) is not None
    add = host.add_action
    def limited(key, title, tooltip, action):
        assert len(actions) < 12
        add(key, title, tooltip, action)
    host.add_action = limited
    assert actions["preview"][2]()
    deliver(tasks)
    old = actions["public-0"][2]
    seen = set()
    for _ in range(3):
        seen.update(k for k in actions if k.startswith(("public-", "private-")))
        more = [v[2] for k, v in actions.items() if k.startswith("catalogue-more-")]
        if more:
            assert more[0]()
            deliver(tasks)
    assert seen == ({f"public-{i}" for i in range(8)}
                    | {f"private-{i}" for i in range(16)})
    assert old() is False
    assert not calls and not toasts
