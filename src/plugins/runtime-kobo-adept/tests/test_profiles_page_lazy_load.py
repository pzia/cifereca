from __future__ import annotations

import importlib.util
from pathlib import Path

from cifereca.plugin_api import AdeptProfileRef


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cifereca_kobo_adept_profiles",
    ROOT / "cifereca_kobo_adept_profiles.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class FakeWidget:
    def __init__(self, mapped: bool = False):
        self.mapped = mapped
        self.callback = None

    def connect(self, signal, callback):
        assert signal == "map"
        self.callback = callback
        return 1

    def get_mapped(self):
        return self.mapped

    def emit_map(self):
        assert self.callback is not None
        self.mapped = True
        self.callback(self)


def test_profile_listing_waits_for_first_map_and_runs_once():
    widget = FakeWidget()
    calls = []

    mod._run_once_on_first_map(widget, lambda: calls.append("profiles"))
    assert calls == []

    widget.emit_map()
    assert calls == ["profiles"]

    widget.emit_map()
    assert calls == ["profiles"]


def test_already_mapped_page_runs_immediately_once():
    widget = FakeWidget(mapped=True)
    calls = []

    mod._run_once_on_first_map(widget, lambda: calls.append("profiles"))
    assert calls == ["profiles"]

    widget.emit_map()
    assert calls == ["profiles"]



class FakeProfilesBroker:
    def __init__(self):
        self.calls = []

    def profiles(self):
        self.calls.append("profiles")
        return (
            AdeptProfileRef(
                profile_id="stable-alexandre",
                name="alexandre",
                complete=True,
                fetch_capable=True,
                acsm_fetch_ready=True,
            ),
        )

    def provision(self, name, username):
        self.calls.append(("provision", name, username))
        return AdeptProfileRef(
            profile_id="stable-" + name,
            name=name,
            complete=True,
            fetch_capable=True,
            acsm_fetch_ready=True,
        )


def test_refresh_task_lists_profiles_once():
    broker = FakeProfilesBroker()

    value, payload = mod._run_profile_task(
        broker, broker.profiles, reload_profiles=False
    )

    assert value == payload
    assert payload[0].name == "alexandre"
    assert broker.calls == ["profiles"]


def test_mutation_task_reloads_profiles_inside_worker():
    broker = FakeProfilesBroker()

    def mutate():
        broker.calls.append("mutate")
        return {"ok": True}

    value, payload = mod._run_profile_task(
        broker, mutate, reload_profiles=True
    )

    assert value == {"ok": True}
    assert payload[0].name == "alexandre"
    assert broker.calls == ["mutate", "profiles"]


def test_profile_page_uses_public_broker_for_listing_and_provisioning():
    source = (ROOT / "cifereca_kobo_adept_profiles.py").read_text(encoding="utf-8")
    assert 'context.broker("adept.profiles")' in source
    assert "profiles_broker.profiles" in source
    assert "profiles_broker.provision" in source
    assert "self.runtime.profiles" not in source
    assert "self.runtime.provision_profile" not in source


def test_deferred_profile_actions_keep_original_arguments_and_ports(monkeypatch):
    """Use the actual page callbacks, with GTK reads forbidden in the worker."""
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from threading import get_ident
    from types import SimpleNamespace as NS

    from cifereca.plugin_api import Cancelled

    ui_thread = get_ident()
    widgets = {}

    class Widget:
        def __init__(self, *args, **kwargs):
            self.callbacks = {}
            self.text = ""
            self.active = False
            self.children = []
            if "label" in kwargs:
                widgets[kwargs["label"]] = self

        def __getattr__(self, field):
            if field.startswith("set_") or field == "add_css_class":
                return lambda *args: None
            raise AttributeError(field)

        def append(self, child):
            self.children.append(child)

        def remove(self, child):
            self.children.remove(child)

        def get_first_child(self):
            return self.children[0] if self.children else None

        def get_next_sibling(self):
            return None

        def connect(self, signal, callback):
            self.callbacks[signal] = callback

        def click(self):
            self.callbacks["clicked"](self)

        def get_mapped(self):
            return False

        def set_placeholder_text(self, text):
            widgets[text] = self

        def get_text(self):
            assert get_ident() == ui_thread, "GTK field read in worker"
            return self.text

        def set_text(self, text):
            self.text = text

        def get_active(self):
            assert get_ident() == ui_thread
            return self.active

        def set_active(self, active):
            self.active = active
            if "toggled" in self.callbacks:
                self.callbacks["toggled"](self)

    idle = []
    gtk = NS(**{name: Widget for name in (
        "ScrolledWindow", "Box", "Label", "Entry", "Button", "CheckButton",
    )}, PolicyType=NS(NEVER=0, AUTOMATIC=1),
        Orientation=NS(VERTICAL=0, HORIZONTAL=1), Align=NS(FILL=0))
    monkeypatch.setitem(sys.modules, "gi", NS(repository=NS(
        Gtk=gtk, GLib=NS(idle_add=idle.append),
    )))
    monkeypatch.setitem(sys.modules, "gi.repository", NS(
        Gtk=gtk, GLib=NS(idle_add=idle.append),
    ))

    class Handle:
        def __init__(self, operation):
            self.operation = operation
            self.cancelled = False
            self.callback = None
            self.error = None
            self.value = None

        def cancel(self):
            self.cancelled = True

        def check_cancelled(self):
            if self.cancelled:
                raise Cancelled("task cancelled")

        def add_done_callback(self, callback):
            self.callback = callback

        def result(self):
            if self.error is not None:
                raise self.error
            return self.value

        def run(self, pool):
            try:
                self.value = pool.submit(self.operation, self).result()
            except Exception as exc:
                self.error = exc
            self.callback(self)

    class Tasks:
        def __init__(self):
            self.handles = []

        def submit(self, operation, **kwargs):
            handle = Handle(operation)
            self.handles.append(handle)
            return handle

    def ports():
        broker = FakeProfilesBroker()
        runtime_calls = []
        runtime = NS(
            associate_profile=lambda *args: runtime_calls.append(("associate", *args)),
            remove_profile=lambda *args: runtime_calls.append(("remove", *args)),
        )
        tasks = Tasks()
        context = NS(
            broker=lambda key: {"adept.profiles": broker, "tasks": tasks}[key],
            service=lambda key: runtime,
        )
        return context, broker, runtime_calls, tasks

    page = mod.AdeptProfilesPagePlugin()
    first, broker, calls, tasks = ports()
    page.activate(first)
    toasts = []
    page._build(NS(toast=toasts.append))
    name = widgets["Nom du profil"]
    email = widgets["Adresse Adobe (email)"]
    mount = widgets["Point de montage de la liseuse"]
    confirm = widgets["Confirmer explicitement la suppression du profil saisi"]
    name.set_text("first")
    email.set_text("first@example.invalid")
    widgets["Créer / activer"].click()
    name.set_text("reader")
    mount.set_text("/synthetic/reader")
    widgets["Associer la liseuse"].click()
    name.set_text("delete-this")
    confirm.set_active(True)
    widgets["Supprimer"].click()

    # Alter every field and even the ambient broker after enqueue.
    name.set_text("wrong")
    email.set_text("wrong@example.invalid")
    mount.set_text("/wrong")
    second, foreign, foreign_calls, foreign_tasks = ports()
    page.profiles_broker = foreign
    page.runtime = second.service("runtime.kobo-adept")
    with ThreadPoolExecutor(max_workers=1) as pool:
        for handle in tasks.handles:
            handle.run(pool)
        assert all(handle.error is None for handle in tasks.handles)
        assert broker.calls == [
            ("provision", "first", "first@example.invalid"),
            "profiles", "profiles", "profiles",
        ]
        assert calls == [
            ("associate", "reader", "/synthetic/reader"), ("remove", "delete-this"),
        ]
        assert foreign.calls == [] and foreign_calls == [] and foreign_tasks.handles == []
        for callback in idle:
            callback()
        idle.clear()
        assert len(toasts) == 3

        # A real activation revocation still prevents queued execution and
        # suppresses old callbacks, even if the same singleton is activated again.
        name.set_text("never-delete")
        confirm.set_active(True)
        widgets["Supprimer"].click()
        pending = tasks.handles[-1]
        page.deactivate()
        page.activate(second)
        pending.run(pool)
        assert isinstance(pending.error, Cancelled)
        assert len(calls) == 2 and foreign_calls == []
        for callback in idle:
            callback()
        assert len(toasts) == 3
        # Late click from the stale page must not queue a second activation.
        widgets["Créer / activer"].click()
        assert len(tasks.handles) == 4 and foreign_tasks.handles == []
