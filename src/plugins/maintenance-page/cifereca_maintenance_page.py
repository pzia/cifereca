"""Container only. All maintenance admission and execution belong to Core."""
from weakref import WeakSet

from cifereca.plugin_api import UiPageContribution
from cifereca.plugin_api.maintenance_page import MaintenancePageContent
from cifereca.plugin_api.page_lifecycle import PageLifecycleV1, PageViewContext


class PageController:
    def __init__(self, host, active, render):
        self.host, self.active, self.render = host, active, render
        self.context = self.content = None
        self.disposed = False
        self._generation = 0

    def _retire(self):
        self._generation += 1
        previous, self.content = self.content, None
        if previous is not None:
            try:
                previous.dispose()
            except Exception:
                pass

    def current(self, context):
        return (not self.disposed and self.active()
                and type(context) is PageViewContext and context.route == "tools"
                and context is self.context and context.is_current())

    def on_context_changed(self, context):
        if self.disposed:
            return
        self.context = None
        self._retire()
        self.render(None, "waiting")

    def on_show(self, context):
        if (self.disposed or not self.active() or type(context) is not PageViewContext
                or context.route != "tools" or not context.is_current()):
            return
        self.context = context
        self.refresh()

    def refresh(self):
        context = self.context
        if not self.current(context):
            return False
        self._retire()
        generation = self._generation
        self.render(None, "loading")
        if self._generation != generation or not self.current(context):
            return False
        try:
            result = self.host.mount_maintenance_tools(context)
        except Exception:
            result = None
        if self._generation != generation or not self.current(context):
            if type(result) is MaintenancePageContent:
                try:
                    result.dispose()
                except Exception:
                    pass
            return False
        if type(result) is not MaintenancePageContent:
            self.render(None, "unavailable")
            return False
        self.content = result
        try:
            self.render(result.widget, "ready")
        except Exception:
            self._retire()
            return False
        return self.current(context) and self._generation == generation

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.context = None
        self._retire()


class MaintenancePagePlugin:
    def __init__(self):
        self.activation = None
        self.controllers = WeakSet()

    def activate(self, context):
        self.activation = object()
        return self

    def deactivate(self):
        self.activation = None
        for controller in tuple(self.controllers):
            controller.dispose()
        self.controllers.clear()

    def pages(self):
        if self.activation is None:
            return ()
        return (UiPageContribution(
            route="tools", title="Outils", icon_name="applications-system-symbolic",
            order=8, factory=self.build, group="configuration",
        ),)

    def controller(self, host, render):
        marker = self.activation
        if marker is None:
            raise RuntimeError("maintenance_page_inactive")
        controller = PageController(host, lambda: self.activation is marker, render)
        self.controllers.add(controller)
        return controller

    def build(self, host):
        from gi.repository import Gtk
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for edge in ("top", "bottom", "start", "end"):
            getattr(outer, "set_margin_" + edge)(20)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Outils", xalign=0, hexpand=True)
        title.add_css_class("page-title")
        refresh = Gtk.Button(label="Actualiser", icon_name="view-refresh-symbolic")
        header.append(title)
        header.append(refresh)
        outer.append(header)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.append(body)
        scroll.set_child(outer)

        def render(widget, state):
            while body.get_first_child() is not None:
                body.remove(body.get_first_child())
            if state == "ready":
                body.append(widget)
            else:
                message = {
                    "waiting": "Outils en attente de contexte.",
                    "loading": "Chargement des contributions…",
                    "unavailable": "Outils de maintenance indisponibles.",
                }[state]
                label = Gtk.Label(label=message, xalign=0, wrap=True)
                label.add_css_class("dim-label")
                body.append(label)
            refresh.set_sensitive(state in ("ready", "unavailable"))

        controller = self.controller(host, render)
        refresh.connect("clicked", lambda _b: controller.refresh())
        render(None, "waiting")
        scroll.cifereca_page_lifecycle_v1 = PageLifecycleV1(
            on_context_changed=controller.on_context_changed,
            on_show=controller.on_show, on_dispose=controller.dispose,
        )
        return scroll


PLUGIN = MaintenancePagePlugin()
