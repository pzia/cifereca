"""Explicit Settings controls for the existing bounded Core UI performance monitor.

The capture is ephemeral and process-local. Settings does not persist the
choice, start observations on entry, schedule tasks or send network traffic.
This controller owns only GTK signals and a native local JSON save dialog.
"""
from __future__ import annotations

import json
from pathlib import Path

from cifereca.plugin_api import prefer_downloads_for_save


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
        try:
            report = monitor.performance_report()
        except Exception:
            report = {}
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

    def record_export(code: str, *, success: bool = False) -> None:
        """Only closed reason codes reach Diagnostics; never log the destination."""
        diagnostics = getattr(window, "diagnostics", None)
        add = getattr(diagnostics, "add", None)
        if not callable(add):
            return
        try:
            add(
                "UI", "performance export", "ok" if success else "error",
                detail={"code": code}, level="detailed",
            )
        except Exception:
            # Diagnostics may be disabled; exporting remains independent.
            pass

    def export_error(code: str, message: str) -> None:
        record_export(code)
        if alive():
            window.show_error(f"{message} ({code}).")

    def export_performance(_button=None) -> None:
        if not alive():
            return
        monitor = observer()
        if monitor is None:
            return
        try:
            report = monitor.performance_report()
        except Exception:
            export_error("report_invalid", "Rapport de performances indisponible")
            return
        if not isinstance(report, dict) or report.get("schema") != "cifereca.performance.phases/1":
            export_error("report_invalid", "Rapport de performances indisponible")
            return
        try:
            # The monitor owns this versioned, bounded, pre-redacted schema.
            # Non-finite values and non-JSON objects are rejected, not coerced.
            content = json.dumps(
                report, ensure_ascii=False, sort_keys=True, indent=2,
                allow_nan=False,
            ) + "\n"
        except (TypeError, ValueError, OverflowError, RecursionError):
            export_error("serialization_failed", "Rapport de performances indisponible")
            return

        chooser = None
        try:
            chooser = Gtk.FileChooserNative.new(
                "Exporter les mesures de performances",
                window, Gtk.FileChooserAction.SAVE,
                "Exporter", "Annuler",
            )
            prefer_downloads_for_save(chooser)
            chooser.set_current_name("cifereca-performances.json")

            def chosen(dialog, response):
                try:
                    # A stale Settings reply must never export or notify.
                    if not alive() or response != Gtk.ResponseType.ACCEPT:
                        return
                    try:
                        selected = dialog.get_file()
                    except Exception:
                        export_error(
                            "destination_unavailable",
                            "Destination de l’export indisponible",
                        )
                        return
                    if selected is None:
                        export_error(
                            "destination_unavailable",
                            "Destination de l’export indisponible",
                        )
                        return

                    writer = getattr(selected, "replace_contents", None)
                    if callable(writer):
                        # Gtk returns a Gio.File, not necessarily a POSIX
                        # path. Gio handles portal/virtual backends correctly;
                        # never turn a URI into a Path or print its value.
                        try:
                            from gi.repository import Gio
                            result = writer(
                                content.encode("utf-8"), None, False,
                                Gio.FileCreateFlags.NONE, None,
                            )
                            if result is False or (
                                isinstance(result, tuple)
                                and result and result[0] is False
                            ):
                                raise OSError("gio_write_failed")
                        except Exception:
                            export_error(
                                "write_failed",
                                "Impossible d’exporter les mesures de performances",
                            )
                            return
                    else:
                        # Compatible with non-GI local chooser doubles and
                        # backends exposing only a local filesystem path.
                        get_path = getattr(selected, "get_path", None)
                        try:
                            path = get_path() if callable(get_path) else None
                        except Exception:
                            path = None
                        if not path:
                            export_error(
                                "destination_unavailable",
                                "Destination de l’export indisponible",
                            )
                            return
                        try:
                            Path(path).write_text(content, encoding="utf-8")
                        except Exception:
                            export_error(
                                "write_failed",
                                "Impossible d’exporter les mesures de performances",
                            )
                            return
                    record_export("completed", success=True)
                    window.toast("Mesures de performances enregistrées à l’emplacement choisi")
                finally:
                    try:
                        dialog.destroy()
                    except Exception:
                        pass

            chooser.connect("response", chosen)
            chooser.show()
        except Exception:
            if chooser is not None:
                try:
                    chooser.destroy()
                except Exception:
                    pass
            export_error("chooser_failed", "Sélecteur de fichier indisponible")

    capture_button.connect("clicked", toggle_capture)
    export_button.connect("clicked", export_performance)
    refresh_status()
    return refresh_status
