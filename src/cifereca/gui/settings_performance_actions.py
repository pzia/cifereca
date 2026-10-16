"""Explicit Settings controls for the existing bounded Core UI performance monitor.

The capture is ephemeral and process-local. Settings does not persist the
choice, start observations on entry, schedule tasks or send network traffic.
This controller owns only GTK signals and a native local JSON save dialog.
"""
from __future__ import annotations

import json
from pathlib import Path


def bind_settings_performance_actions(
    window, *, page_lifetime, capture_button, export_button, status_label, Gtk,
):
    """Bind opt-in start/stop and local export; return a cheap status refresh."""

    def alive() -> bool:
        return page_lifetime.alive and not getattr(window, "_closed", False)

    def observer():
        monitor = getattr(window, "ui_perf", None)
        if (
            monitor is None
            or not callable(getattr(monitor, "enable_detailed_capture", None))
            or not callable(getattr(monitor, "performance_report", None))
        ):
            return None
        return monitor

    def refresh_status() -> None:
        if not alive():
            return
        monitor = observer()
        if monitor is None:
            status_label.set_label("Métrologie Core indisponible.")
            capture_button.set_sensitive(False)
            export_button.set_sensitive(False)
            return
        active = monitor.detailed_capture_enabled is True
        # The report is a local in-memory snapshot, not a provider call.
        report = monitor.performance_report()
        stats = report.get("stats", {}) if isinstance(report, dict) else {}
        count = sum(
            row["count"] for row in stats.values()
            if isinstance(row, dict) and type(row.get("count")) is int
            and 0 <= row["count"] <= 1000
        ) if isinstance(stats, dict) else 0
        origin = report.get("measurement_origin") if isinstance(report, dict) else None
        provenance = {
            "observed": "mesures observées",
            "simulated": "mesures simulées",
        }.get(origin if type(origin) is str else None, "provenance non établie")
        dropped = report.get("events_dropped", 0) if isinstance(report, dict) else 0
        dropped = dropped if type(dropped) is int and dropped >= 0 else 0
        status_label.set_label(
            ("Capture détaillée en cours" if active else "Capture détaillée arrêtée")
            + f" · {provenance} · {count} échantillon(s)"
            + (f" · {dropped} événement(s) écarté(s)" if dropped else "")
            + ("." if not count else ".")
        )
        capture_button.set_label(
            "Arrêter la capture détaillée" if active
            else "Démarrer la capture détaillée"
        )
        capture_button.set_sensitive(True)
        export_button.set_sensitive(True)

    def toggle_capture(_button=None) -> None:
        if not alive():
            return
        monitor = observer()
        if monitor is None:
            return
        try:
            monitor.enable_detailed_capture(
                monitor.detailed_capture_enabled is not True
            )
        except Exception:
            window.show_error("Impossible de modifier la capture de performances.")
        refresh_status()

    def export_performance(_button=None) -> None:
        if not alive():
            return
        monitor = observer()
        if monitor is None:
            return
        try:
            report = monitor.performance_report()
            if not isinstance(report, dict) or report.get("schema") != "cifereca.performance.phases/1":
                raise ValueError("invalid_performance_schema")
            # Serialize *only* the Core monitor's bounded, pre-redacted
            # structured schema. No diagnostics text, paths, labels or logs.
            content = json.dumps(
                report, ensure_ascii=False, sort_keys=True, indent=2,
                allow_nan=False,
            ) + "\n"
        except (TypeError, ValueError, OverflowError):
            window.show_error("Rapport de performances indisponible.")
            return

        chooser = Gtk.FileChooserNative.new(
            "Exporter les mesures de performances",
            window, Gtk.FileChooserAction.SAVE,
            "Exporter", "Annuler",
        )
        chooser.set_current_name("cifereca-performances.json")

        def chosen(dialog, response):
            try:
                # A response to a stale Settings instance must never write a
                # local file or announce a successful export.
                if not alive() or response != Gtk.ResponseType.ACCEPT:
                    return
                selected = dialog.get_file()
                path = selected.get_path() if selected is not None else None
                if not path:
                    raise ValueError("invalid_destination")
                Path(path).write_text(content, encoding="utf-8")
                window.toast("Mesures de performances exportées localement")
            except Exception:
                # Never embed a filename, provider path or arbitrary error in
                # a toast, log or diagnostic notification.
                if alive():
                    window.show_error("Impossible d’exporter les mesures de performances.")
            finally:
                dialog.destroy()

        chooser.connect("response", chosen)
        chooser.show()

    capture_button.connect("clicked", toggle_capture)
    export_button.connect("clicked", export_performance)
    refresh_status()
    return refresh_status
