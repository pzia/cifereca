"""P1 #1741: user-initiated real Core monitor capture and redacted local export.

The Settings controller is imported without GI and uses native chooser doubles.
No fixture contacts a provider or writes a file before an explicit export.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.settings_lifetime import SettingsPageLifetime
from cifereca.gui.settings_performance_actions import bind_settings_performance_actions
from cifereca.gui.ui_perf import UiPerfMonitor


class Button:
    def __init__(self):
        self.signals = {}
        self.label = ""
        self.sensitive = True

    def connect(self, signal, callback):
        assert signal not in self.signals
        self.signals[signal] = callback

    def set_label(self, value):
        self.label = value

    def set_sensitive(self, value):
        self.sensitive = value

    def click(self):
        self.signals["clicked"](self)


class File:
    def __init__(self, path):
        self.path = path

    def get_path(self):
        return str(self.path)


class Chooser:
    seen = []

    def __init__(self):
        self.signals = {}
        self.name = None
        self.shown = False
        self.destroyed = False
        self.file = None

    @classmethod
    def new(cls, *args):
        instance = cls()
        cls.seen.append(instance)
        return instance

    def set_current_name(self, name):
        self.name = name

    def connect(self, signal, callback):
        self.signals[signal] = callback

    def show(self):
        self.shown = True

    def get_file(self):
        return self.file

    def respond(self, value, path=None):
        self.file = File(path) if path is not None else None
        self.signals["response"](self, value)

    def destroy(self):
        self.destroyed = True


GTK = NS(FileChooserNative=Chooser,
         FileChooserAction=NS(SAVE="save"),
         ResponseType=NS(ACCEPT="accept", CANCEL="cancel"))


def _page(monitor=None, *, host=None):
    monitor = monitor or UiPerfMonitor()
    calls = []
    if host is None:
        host = NS(
            _closed=False,
            ui_perf=monitor,
            show_error=lambda *args: calls.append(("error", args)),
            toast=lambda *args: calls.append(("toast", args)),
        )
    lifetime = SettingsPageLifetime()
    start, export, status = Button(), Button(), Button()
    refresh = bind_settings_performance_actions(
        host, page_lifetime=lifetime, capture_button=start,
        export_button=export, status_label=status, Gtk=GTK,
    )
    return NS(host=host, lifetime=lifetime, start=start, export=export,
              status=status, refresh=refresh, calls=calls, monitor=monitor)


def test_opening_settings_is_passive_and_capture_requires_explicit_click():
    page = _page()
    assert not page.monitor.detailed_capture_enabled
    assert page.monitor.performance_report()["no_samples"]
    assert page.start.label == "Démarrer la capture détaillée"
    assert "Capture détaillée arrêtée" in page.status.label
    assert "0 échantillon(s)" in page.status.label
    page.monitor.record_phase("task", "worker", 4)
    assert page.monitor.performance_report()["events"] == []
    page.refresh()
    assert "1 échantillon(s)" in page.status.label
    page.start.click()
    assert page.monitor.detailed_capture_enabled
    assert page.start.label == "Arrêter la capture détaillée"
    page.monitor.record_phase("task", "worker", 5)
    assert len(page.monitor.performance_report()["events"]) == 1
    page.start.click()
    assert not page.monitor.detailed_capture_enabled
    page.monitor.record_phase("task", "worker", 6)
    assert len(page.monitor.performance_report()["events"]) == 1
    assert page.calls == []


def test_native_export_is_manual_json_bounded_and_redacted(tmp_path):
    Chooser.seen.clear()
    page = _page(UiPerfMonitor(measurement_origin="simulated", max_events=2))
    secret = "https://private.invalid/?token=SECRET_ACCOUNT"
    page.start.click()
    for index in range(3):
        assert page.monitor.record_phase(
            secret, "worker", index + 1, operation_id=index + 1,
            trigger=secret, outcome=secret,
        )
    page.export.click()
    assert len(Chooser.seen) == 1
    dialog = Chooser.seen[0]
    assert dialog.name == "cifereca-performances.json" and dialog.shown
    path = tmp_path / "metrics.json"
    assert not path.exists()
    dialog.respond(GTK.ResponseType.ACCEPT, path)
    assert dialog.destroyed and path.is_file()
    raw = path.read_text("utf-8")
    assert "SECRET_ACCOUNT" not in raw and "https://" not in raw
    report = json.loads(raw)
    assert report["schema"] == "cifereca.performance.phases/1"
    assert report["measurement_origin"] == "simulated"
    assert report["events_dropped"] == 1
    assert report["remote_upload"] is False
    assert [r["category"] for r in report["events"]] == ["other", "other"]
    assert len(report["events"]) == 2
    assert report["stats"]["other.worker"]["count"] == 3
    assert page.calls == [("toast", ("Mesures de performances exportées localement",))]


def test_cancel_and_late_file_chooser_never_write_or_toast(tmp_path):
    Chooser.seen.clear()
    page = _page()
    target = tmp_path / "cancel.json"
    page.export.click()
    dialog = Chooser.seen[-1]
    dialog.respond(GTK.ResponseType.CANCEL, target)
    assert dialog.destroyed and not target.exists() and page.calls == []
    page.export.click()
    dialog = Chooser.seen[-1]
    page.lifetime.dispose()
    dialog.respond(GTK.ResponseType.ACCEPT, target)
    assert dialog.destroyed and not target.exists() and page.calls == []
    # Stale Settings buttons are revoked on rebuild/close.
    page.start.click()
    page.export.click()
    assert not page.monitor.detailed_capture_enabled
    assert len(Chooser.seen) == 2


def test_capture_is_process_local_and_continues_across_settings_rebuild():
    monitor = UiPerfMonitor()
    owner = _page(monitor)
    owner.start.click()
    assert monitor.detailed_capture_enabled
    owner.lifetime.dispose()
    replacement = _page(monitor)
    assert monitor.detailed_capture_enabled
    assert replacement.start.label == "Arrêter la capture détaillée"
    owner.start.click()
    assert monitor.detailed_capture_enabled
    replacement.start.click()
    assert not monitor.detailed_capture_enabled


def test_window_closure_blocks_captures_and_export(tmp_path):
    Chooser.seen.clear()
    page = _page()
    page.host._closed = True
    page.start.click()
    page.export.click()
    assert not page.monitor.detailed_capture_enabled
    assert Chooser.seen == []
    page.refresh()
    assert page.start.label == "Démarrer la capture détaillée"


def test_invalid_report_fails_without_exposing_payload_in_error():
    class HostMonitor:
        detailed_capture_enabled = False
        def enable_detailed_capture(self, enabled):
            self.detailed_capture_enabled = enabled
        def performance_report(self):
            return {"schema": "wrong", "secret": "PRIVATE_TOKEN"}
    page = _page(HostMonitor())
    page.export.click()
    assert page.calls == [("error", ("Rapport de performances indisponible.",))]
    assert "PRIVATE_TOKEN" not in str(page.calls)


def test_synthetic_and_real_sources_are_labeled_differently():
    simulated = _page(UiPerfMonitor(measurement_origin="simulated"))
    observed = _page(UiPerfMonitor(measurement_origin="observed"))
    assert "mesures simulées" in simulated.status.label
    assert "mesures observées" in observed.status.label
