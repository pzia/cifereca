"""Generic contextual host admission and revocation; no GTK or native fallback."""
from types import SimpleNamespace as NS
import pytest

from cifereca.gui.guided_book_review_host import mount_guided_book_reviews
from cifereca.plugin_api import ResolvedPluginService


class Button:
    def set_tooltip_text(self, value):
        self.tooltip = value
    def connect(self, signal, callback):
        self.click = lambda: callback(self)
    def get_mapped(self):
        return True


def rig():
    tasks, buttons, groups, calls, disposed = [], [], [], [], []
    class Plugin:
        def contribute_book_review(self, host):
            def invoke():
                host.submit("private label", lambda: calls.append(True) or 42,
                            lambda value: host.add_group("Revue", (("Résultat", str(value)),)))
            host.add_action("review", "Revoir", "Lecture seule", invoke)
            return lambda: disposed.append(True)
    plugin = Plugin()
    resolved = ResolvedPluginService("fixture", "1", "pkg", "1", "maintenance.tool",
                                     plugin, registry_generation=3)
    manager = NS(live=plugin, _registry_generation=3)
    manager.service = lambda key: manager.live
    manager.services = lambda category: (resolved,) if manager.live is plugin else ()
    row, token = NS(key="book"), object()
    item = {"identity_id": "A", "placement_id": "placement", "local": True}
    panel = NS(add_actions=buttons.extend, add_group=lambda *args: groups.append(args))
    service = NS(current_identity=NS(identity_id="A"), storage=object(),
                 _source_context_epoch=4, _account_context_epoch=1)
    window = NS(service=service, plugin_manager=manager,
                runner=NS(submit=lambda *args, **kw: tasks.append((args, kw))),
                _route_display_token=object(), _public_web_selection_library=token,
                _identity_context_generation=2, _library_objects={"book": item},
                library_page=NS(selection=NS(get_selected_item=lambda: row)),
                view_stack=NS(get_visible_child_name=lambda: "library"),
                action_button=lambda *args: Button(), toast=lambda *a: None)
    mount = lambda: mount_guided_book_reviews(window, panel, row, item, token=token)
    return window, item, mount, tasks, buttons, groups, calls, disposed


def test_generic_contribution_mount_is_inert_and_worker_delivers_once():
    window, item, mount, tasks, buttons, groups, calls, disposed = rig()
    host, = mount()
    assert host.placement_id == "placement" and host.identity_id == "A"
    assert not hasattr(host, "window") and not hasattr(host, "storage")
    button, = buttons
    assert not tasks and not calls
    button.click()
    button.click()
    assert len(tasks) == 1
    args, kw = tasks[0]
    assert args[0] == "Revue documentaire locale…" and kw["origin"] == "user"
    result = args[1]()
    args[2](result)
    args[2](result)
    assert calls == [True] and groups == [("Revue", [("Résultat", "42")])]
    host.dispose()
    host.dispose()
    button.click()
    assert len(tasks) == 1 and disposed == [True]


@pytest.mark.parametrize("change", ["disable", "registry", "selection", "route", "row",
                                    "placement", "epoch", "account", "owner", "close", "dispose"])
@pytest.mark.parametrize("phase", ["before", "after"])
def test_revoked_selection_prevents_worker_or_result(change, phase):
    window, item, mount, tasks, buttons, groups, calls, disposed = rig()
    host, = mount()
    buttons[0].click()
    args, _ = tasks[0]
    if phase == "after":
        result = args[1]()
    if change == "disable":
        window.plugin_manager.live = None
    elif change == "registry":
        window.plugin_manager._registry_generation += 1
    elif change == "selection":
        window._public_web_selection_library = object()
    elif change == "route":
        window._route_display_token = object()
    elif change == "row":
        window._library_objects["book"] = dict(item)
    elif change == "placement":
        item["placement_id"] = "replacement"
    elif change == "epoch":
        window.service._source_context_epoch += 1
    elif change == "account":
        window.service._account_context_epoch = True
    elif change == "owner":
        window.service.current_identity = NS(identity_id="A")
    elif change == "close":
        window._closed = True
    else:
        host.dispose()
    if phase == "before":
        assert args[1]() is None
        result = None
    args[2](result)
    assert not groups
    buttons[0].click()
    assert len(tasks) == 1 and len(calls) == (phase == "after")


@pytest.mark.parametrize("change", ["foreign", "unknown", "canonical", "disabled"])
def test_unadmitted_item_never_mounts_contribution(change):
    window, item, mount, tasks, buttons, groups, calls, disposed = rig()
    if change == "foreign":
        item["identity_id"] = "B"
    elif change == "unknown":
        item.pop("placement_id")
    elif change == "canonical":
        item["_public_canonical_document_link"] = object()
    else:
        window.plugin_manager.live = None
    assert mount() == ()
    assert not tasks and not buttons
