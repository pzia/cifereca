"""#7100 page contribution tests, independent of any functional tool."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api.maintenance_page import MaintenancePageContent
from cifereca.plugin_api.page_lifecycle import PageViewContext
from cifereca.gui.page_lifecycle_controller import PageLifecycleDispatcher
from cifereca.plugin_api.page_lifecycle import PageLifecycleV1

ROOT = Path(__file__).resolve().parents[1] / "src/plugins/maintenance-page"


def plugin():
    spec = importlib.util.spec_from_file_location("maintenance_page_test", ROOT / "cifereca_maintenance_page.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PLUGIN.activate(NS())


def rig():
    p = plugin()
    mounts, disposed, rendered, errors = [], [], [], []
    host = NS()
    def mount(context):
        value = (context.identity_id, len(mounts))
        mounts.append(value)
        return MaintenancePageContent(value, lambda: disposed.append(value))
    host.mount_maintenance_tools = mount
    controller = p.controller(host, lambda *args: rendered.append(args))
    lifecycle = PageLifecycleDispatcher(lambda: True, errors.append)
    widget = NS(cifereca_page_lifecycle_v1=PageLifecycleV1(
        controller.on_context_changed, controller.on_show, controller.dispose,
    ))
    lifecycle.context_changed("A", None)
    assert lifecycle.mount("tools", widget)
    return p, host, controller, lifecycle, mounts, disposed, rendered, errors


def test_package_declares_stable_optional_route_without_tool_dependency():
    descriptor, = json.loads((ROOT / "cifereca-plugin.json").read_text())["plugins"]
    assert descriptor["type"] == "ui.page"
    assert descriptor["requires"] == []
    assert descriptor["requires_brokers"] == ["maintenance.page.host"]
    assert descriptor["enabled_by_default"] is True
    p = plugin()
    page, = p.pages()
    assert page.route == "tools" and page.group == "configuration"
    p.deactivate()
    assert p.pages() == ()


def test_only_visible_lifecycle_mounts_and_refreshes_generic_content():
    p, host, c, lifecycle, mounts, disposed, rendered, errors = rig()
    assert not mounts and not c.refresh()
    lifecycle.show("library")
    assert not mounts
    lifecycle.show("tools")
    assert mounts == [("A", 0)] and rendered[-1] == (("A", 0), "ready")
    lifecycle.show("tools")
    assert len(mounts) == 1
    assert c.refresh()
    assert disposed == [("A", 0)] and mounts[-1] == ("A", 1)
    lifecycle.show("library")
    assert not c.refresh()
    lifecycle.context_changed("B", None)
    assert disposed[-1] == ("A", 1)
    assert rendered[-1] == (None, "waiting")
    lifecycle.show("tools")
    assert mounts[-1] == ("B", 2)
    lifecycle.dispose()
    p.deactivate()
    assert disposed == mounts
    assert not c.refresh() and not errors


@pytest.mark.parametrize("change", ["identity", "route", "detach", "disable"])
def test_mount_reentering_lifecycle_disposes_abandoned_content(change):
    p, host, c, lifecycle, mounts, disposed, rendered, errors = rig()
    actual = host.mount_maintenance_tools
    triggered = False
    def mount(context):
        nonlocal triggered
        value = actual(context)
        if not triggered:
            triggered = True
            if change == "identity":
                lifecycle.context_changed("B", None)
            elif change == "route":
                lifecycle.show("library")
            elif change == "detach":
                lifecycle.detach("tools")
            else:
                p.deactivate()
        return value
    host.mount_maintenance_tools = mount
    lifecycle.show("tools")
    assert mounts[0] == ("A", 0) and disposed[0] == ("A", 0)
    assert (("A", 0), "ready") not in rendered
    if change == "identity":
        assert rendered[-1] == (("B", 1), "ready")
    else:
        assert not c.refresh()
    lifecycle.dispose()
    assert disposed == mounts and not errors


@pytest.mark.parametrize("failure", [None, "exception", "wrong_type"])
def test_unavailable_content_can_be_retried_without_exception_leak(failure):
    p, host, c, lifecycle, mounts, disposed, rendered, errors = rig()
    actual = host.mount_maintenance_tools
    def mount(_context):
        if failure == "exception":
            raise RuntimeError("private evidence")
        return failure
    host.mount_maintenance_tools = mount
    lifecycle.show("tools")
    assert rendered[-1] == (None, "unavailable")
    host.mount_maintenance_tools = actual
    assert c.refresh()
    assert rendered[-1] == (("A", 0), "ready")
    lifecycle.dispose()
    assert not errors and disposed == mounts
