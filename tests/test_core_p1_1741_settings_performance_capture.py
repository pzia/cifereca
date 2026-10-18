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
    assert page.calls == [("toast", ("Mesures de performances enregistrées à l’emplacement choisi",))]


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
    assert page.calls == [("error", ("Rapport de performances indisponible (report_invalid).",))]
    assert "PRIVATE_TOKEN" not in str(page.calls)


def test_synthetic_and_real_sources_are_labeled_differently():
    simulated = _page(UiPerfMonitor(measurement_origin="simulated"))
    observed = _page(UiPerfMonitor(measurement_origin="observed"))
    assert "mesures simulées" in simulated.status.label
    assert "mesures observées" in observed.status.label


def _gio_testing(monkeypatch):
    """GI is intentionally absent from the Core-only pytest environment."""
    import sys
    from types import ModuleType

    gi = ModuleType("gi")
    gi.__path__ = []
    repository = ModuleType("gi.repository")
    repository.Gio = NS(FileCreateFlags=NS(NONE=0))
    gi.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)


class VirtualDestination:
    """Gio.File-like save target with no host filesystem path."""

    def __init__(self, *, failure=None, result=(True, None)):
        self.failure = failure
        self.result = result
        self.writes = []
        self.path_requests = 0

    def get_path(self):
        self.path_requests += 1
        raise AssertionError("Never convert a Gio.File to a POSIX path")

    def replace_contents(self, data, etag, backup, flags, cancellable):
        self.writes.append((data, etag, backup, flags, cancellable))
        if self.failure is not None:
            raise self.failure
        return self.result


def _page_with_export_events():
    records = []
    monitor = UiPerfMonitor()
    host = NS(
        _closed=False, ui_perf=monitor,
        diagnostics=NS(add=lambda *a, **kw: records.append(("diagnostic", a, kw))),
        toast=lambda *a: records.append(("toast", a)),
        show_error=lambda *a: records.append(("error", a)),
    )
    return _page(monitor, host=host), records


def test_gio_virtual_destination_without_posix_path(monkeypatch):
    _gio_testing(monkeypatch)
    Chooser.seen.clear()
    page, records = _page_with_export_events()
    page.start.click()
    page.monitor.record_phase("library", "projection", 3)
    page.start.click()
    page.export.click()
    chooser = Chooser.seen[-1]
    target = VirtualDestination()
    chooser.file = target
    chooser.signals["response"](chooser, GTK.ResponseType.ACCEPT)
    assert chooser.destroyed
    assert len(target.writes) == 1 and target.path_requests == 0
    data, etag, backup, flags, cancellable = target.writes[0]
    assert (etag, backup, flags, cancellable) == (None, False, 0, None)
    report = json.loads(data.decode("utf-8"))
    assert report["schema"] == "cifereca.performance.phases/1"
    assert report["stats"]["library.projection"]["count"] == 1
    assert ("toast", ("Mesures de performances enregistrées à l’emplacement choisi",)) in records
    assert any(event[2] == {"detail": {"code": "completed"}, "level": "detailed"}
               for event in records if event[0] == "diagnostic")


def test_native_export_failure_is_redacted_and_does_not_claim_success(monkeypatch):
    _gio_testing(monkeypatch)
    Chooser.seen.clear()
    page, records = _page_with_export_events()
    page.export.click()
    chooser = Chooser.seen[-1]
    target = VirtualDestination(failure=OSError("/secret/home/file?token=NEVER_LOG"))
    chooser.file = target
    chooser.signals["response"](chooser, GTK.ResponseType.ACCEPT)
    assert chooser.destroyed
    assert len(target.writes) == 1
    assert ("error", ("Impossible d’exporter les mesures de performances (write_failed).",)) in records
    assert "NEVER_LOG" not in str(records)
    assert not any(event[0] == "toast" for event in records)
    assert any(event[2].get("detail", {}).get("code") == "write_failed"
               for event in records if event[0] == "diagnostic")


def test_missing_gio_target_and_failed_replace_are_typed(monkeypatch):
    _gio_testing(monkeypatch)
    Chooser.seen.clear()
    page, records = _page_with_export_events()
    page.export.click()
    chooser = Chooser.seen[-1]
    chooser.signals["response"](chooser, GTK.ResponseType.ACCEPT)
    assert chooser.destroyed
    assert any("destination_unavailable" in x[1][0] for x in records if x[0] == "error")
    records.clear()
    page.export.click()
    chooser = Chooser.seen[-1]
    chooser.file = VirtualDestination(result=(False, None))
    chooser.signals["response"](chooser, GTK.ResponseType.ACCEPT)
    assert any("write_failed" in x[1][0] for x in records if x[0] == "error")
    assert not any(x[0] == "toast" for x in records)


def test_chooser_failure_and_local_disk_error_are_distinct(monkeypatch, tmp_path):
    Chooser.seen.clear()
    page, records = _page_with_export_events()
    def fail_show(_self):
        raise OSError("/hidden/chooser")
    monkeypatch.setattr(Chooser, "show", fail_show)
    page.export.click()
    assert Chooser.seen[-1].destroyed
    assert any("chooser_failed" in x[1][0] for x in records if x[0] == "error")
    assert "/hidden" not in str(records)
    monkeypatch.undo()
    records.clear()
    page.export.click()
    chooser = Chooser.seen[-1]
    # Attempting to write to a directory fails, without leaking the path.
    chooser.respond(GTK.ResponseType.ACCEPT, tmp_path)
    assert chooser.destroyed
    assert any("write_failed" in x[1][0] for x in records if x[0] == "error")


def test_export_nonfinite_invalid_schema_and_capture_stale_are_safe(monkeypatch):
    Chooser.seen.clear()
    class BrokenReport:
        detailed_capture_enabled = False
        def enable_detailed_capture(self, active):
            self.detailed_capture_enabled = active
        def performance_report(self):
            return {
                "schema": "cifereca.performance.phases/1",
                "measurement_origin": "observed",
                "stats": {"task.worker": {"count": 1, "max_ms": float("nan")}},
            }
    page = _page(BrokenReport())
    page.export.click()
    assert page.calls == [("error", ("Rapport de performances indisponible (serialization_failed).",))]
    assert Chooser.seen == []
    _gio_testing(monkeypatch)
    other, records = _page_with_export_events()
    other.export.click()
    chooser = Chooser.seen[-1]
    target = VirtualDestination()
    chooser.file = target
    other.lifetime.dispose()
    chooser.signals["response"](chooser, GTK.ResponseType.ACCEPT)
    assert chooser.destroyed and target.writes == [] and records == []
