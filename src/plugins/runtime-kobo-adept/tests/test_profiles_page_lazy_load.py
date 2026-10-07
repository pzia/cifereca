from __future__ import annotations

import importlib.util
from pathlib import Path


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



class FakeRuntime:
    def __init__(self):
        self.calls = []

    def profiles(self):
        self.calls.append("profiles")
        return {"profiles": [{"name": "alexandre"}]}


def test_refresh_task_lists_profiles_once():
    runtime = FakeRuntime()

    value, payload = mod._run_profile_task(
        runtime, runtime.profiles, reload_profiles=False
    )

    assert value == payload
    assert payload["profiles"][0]["name"] == "alexandre"
    assert runtime.calls == ["profiles"]


def test_mutation_task_reloads_profiles_inside_worker():
    runtime = FakeRuntime()

    def mutate():
        runtime.calls.append("mutate")
        return {"ok": True}

    value, payload = mod._run_profile_task(
        runtime, mutate, reload_profiles=True
    )

    assert value == {"ok": True}
    assert payload["profiles"][0]["name"] == "alexandre"
    assert runtime.calls == ["mutate", "profiles"]
