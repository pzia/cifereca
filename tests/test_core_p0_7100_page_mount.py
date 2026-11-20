"""#7100: the public page owns a generic scoped content mount."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.maintenance_page_mount import mount_page_content, mount_guided_tool
from cifereca.plugin_api.page_lifecycle import PageViewContext
from cifereca.plugin_api.guided_maintenance import GuidedMaintenanceView
from cifereca.plugin_api.models import ResolvedPluginService


class Widget:
    def __init__(self, **kwargs):
        self.children = []
        self.options = kwargs

    def append(self, value):
        self.children.append(value)


Gtk = NS(Widget=Widget, Box=Widget, Label=Widget, Orientation=NS(VERTICAL=1))


def rig():
    events = []
    owner = NS(identity_id="A")
    window = NS(service=NS(current_identity=owner), _closed=False,
                _route_display_token=object(), _identity_context_generation=1,
                _guided_maintenance_disposers=[], runner=NS())
    def dispose():
        callbacks, window._guided_maintenance_disposers = window._guided_maintenance_disposers, []
        for callback in callbacks:
            callback()
        events.append("dispose")
    def render(*, _page_context):
        events.append(("render", _page_context.identity_id))
        window._tools_box.append(Widget())
    window._dispose_maintenance_lines = dispose
    window._refresh_tools_page = render
    return window, events


def context(identity="A", route="tools", current=lambda _visible: True):
    return PageViewContext(route, identity, None, 1, "show", current)


def test_late_dispose_cannot_revoke_successor():
    window, events = rig()
    first = mount_page_content(window, context(), Gtk=Gtk)
    second = mount_page_content(window, context(), Gtk=Gtk)
    assert first[0] is not second[0] and window._tools_box is second[0]
    first[1]()
    assert window._tools_box is second[0]
    second[1]()
    assert window._tools_box is None
    count = len(events)
    second[1]()
    assert len(events) == count


@pytest.mark.parametrize("ctx", [context("B"), context(route="library"),
                                 context(current=lambda _: False), None])
def test_invalid_context_never_calls_renderer(ctx):
    window, events = rig()
    assert mount_page_content(window, ctx, Gtk=Gtk) is None
    assert not events


def test_reentrant_mount_disposes_only_abandoned_owner():
    window, events = rig()
    live = [True]
    outer = context(current=lambda _: live[0])
    successor = []
    original = window._refresh_tools_page

    def render(*, _page_context):
        if _page_context is outer:
            live[0] = False
            successor.append(mount_page_content(window, context(), Gtk=Gtk))
        else:
            original(_page_context=_page_context)

    window._refresh_tools_page = render
    assert mount_page_content(window, outer, Gtk=Gtk) is None
    assert window._tools_box is successor[0][0]
    successor[0][1]()
    assert window._tools_box is None


@pytest.mark.parametrize("mode", ["success", "failure", "stale", "bad_widget"])
def test_guided_mount_owns_lease_no_fallback(mode):
    window, events = rig()
    leases = []

    class Tool:
        def build_guided(self, lease):
            leases.append(lease)
            if mode == "failure":
                raise RuntimeError("private provider data")
            if mode == "stale":
                window._route_display_token = object()
            return GuidedMaintenanceView(
                Widget() if mode != "bad_widget" else object(),
                lambda: events.append("view_disposed"),
            )

    tool = Tool()
    window.plugin_manager = NS(_registry_generation=2, service=lambda _: tool)
    resolved = ResolvedPluginService("fixture.tool", "1", "fixture", "1",
                                     "maintenance.tool", tool, registry_generation=2)
    box = Widget()
    assert mount_guided_tool(window, box, resolved, Gtk=Gtk)
    assert len(box.children) == 1 and len(leases) == 1
    if mode == "success":
        assert leases[0].is_current()
        window._dispose_maintenance_lines()
        assert events.count("view_disposed") == 1
    else:
        assert not window._guided_maintenance_disposers
        assert "indisponible" in box.children[0].options["label"]
        if mode != "failure":
            assert events.count("view_disposed") == 1
    assert not leases[0].is_current()
    assert not leases[0].submit("stale", lambda: pytest.fail("must not run"), lambda _: None)
    ordinary = ResolvedPluginService("fixture.ordinary", "1", "fixture", "1",
                                     "maintenance.tool", object(), registry_generation=2)
    assert not mount_guided_tool(window, box, ordinary, Gtk=Gtk)


def test_guided_mount_uses_page_lifecycle_not_duplicate_route_token():
    window, events = rig()
    leases = []
    live = [True]

    class Tool:
        def build_guided(self, lease):
            leases.append(lease)
            return GuidedMaintenanceView(Widget(), lambda: events.append("disposed"))

    tool = Tool()
    window.plugin_manager = NS(_registry_generation=2, service=lambda _: tool)
    window.runner.submit = lambda *args, **kw: events.append(args)
    resolved = ResolvedPluginService("fixture.tool", "1", "fixture", "1",
                                     "maintenance.tool", tool, registry_generation=2)
    assert mount_guided_tool(window, Widget(), resolved, Gtk=Gtk,
                             view_current=lambda: live[0])
    lease, = leases
    window._route_display_token = object()  # repeat show("tools"), not a departure
    assert lease.is_current()
    received = []
    assert lease.submit("attempt", lambda: "read-only result", received.append)
    args = events[-1]
    result = args[1]()
    live[0] = False  # actual PageLifecycle transition
    args[2](result)
    assert not received and not lease.is_current()
    window._dispose_maintenance_lines()
    assert events.count("disposed") == 1
