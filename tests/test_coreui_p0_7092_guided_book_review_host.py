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
    def add_review_group(*args):
        groups.append(args)
        return lambda: groups.remove(args) if args in groups else None
    panel = NS(add_actions=buttons.extend, add_review_group=add_review_group)
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
    assert len(tasks) == 1 and disposed == [True] and not groups


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


@pytest.mark.parametrize("failure", ["exception", "bad_disposer", "revocation"])
def test_mount_cannot_submit_render_or_publish_failed_contribution(failure):
    window, item, mount, tasks, buttons, groups, calls, disposed = rig()
    plugin = window.plugin_manager.live

    def contribute(host):
        assert not host.submit("mount", lambda: pytest.fail("mount submitted"),
                               lambda result: pytest.fail("mount delivered"))
        host.add_group("Unexpected", (("Row", "Value"),))
        host.add_action("first", "Preview", "Read only", lambda: None)
        assert not tasks and not groups and not buttons
        if failure == "exception":
            raise RuntimeError("private supplier exception")
        if failure == "bad_disposer":
            return None
        window._route_display_token = object()
        return lambda: disposed.append(True)

    plugin.contribute_book_review = contribute
    assert mount() == ()
    assert not tasks and not buttons and not groups
    assert disposed == ([True] if failure == "revocation" else [])


def test_delivery_cannot_submit_new_work_and_idle_host_cannot_render():
    window, item, mount, tasks, buttons, groups, calls, disposed = rig()
    plugin = window.plugin_manager.live
    captured = []

    def contribute(host):
        captured.append(host)

        def finish(value):
            assert not host.submit("recursive", lambda: pytest.fail("recursive work"),
                                   lambda result: None)
            host.add_group("Result", (("Row", str(value)),))

        def click():
            assert host.submit("click", lambda: 42, finish)
            assert not host.submit("second", lambda: pytest.fail("second work"),
                                   lambda result: None)
        host.add_action("first", "Review", "Read only", click)
        return lambda: None

    plugin.contribute_book_review = contribute
    host, = mount()
    assert captured == [host]
    assert not host.submit("idle", lambda: pytest.fail("idle work"), lambda _: None)
    host.add_group("Idle", (("Row", "Value"),))
    buttons[0].click()
    args, _ = tasks[0]
    args[2](args[1]())
    assert len(tasks) == 1 and groups == [("Result", [("Row", "42")])]


def test_worker_checks_use_no_gtk_and_non_ui_delivery_cannot_render():
    from threading import Thread, get_ident
    window, item, mount, tasks, buttons, groups, calls, disposed = rig()
    ui = get_ident()

    def visible():
        assert get_ident() == ui
        return "library"

    window.view_stack.get_visible_child_name = visible
    host, = mount()
    buttons[0].click()
    args, _ = tasks[0]
    results = []
    worker = Thread(target=lambda: results.append(args[1]()))
    worker.start()
    worker.join()
    assert results == [42] and calls == [True]
    worker = Thread(target=lambda: args[2](results[0]))
    worker.start()
    worker.join()
    assert not groups
