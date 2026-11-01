#!/usr/bin/env python3
"""P1 #4093: real GTK GUI --capture normal-close acceptance, synthetic offline.

Run only inside the existing E2E Xvfb/D-Bus harness. The real cifereca GUI is
launched with --no-plugins in the process's actual GTK main loop. A mapped
toplevel is closed via Gtk.Window.close() (not SIGTERM/SIGKILL). Validate the
resulting private ZIP and delete it before E2E CI can publish artifacts.
No online provider, browser, credentials or real user configuration.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import zipfile

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from cifereca import app
from cifereca.core.capture_session_export import validate_capture_session


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1366,), required=True)
    args = parser.parse_args()
    if not os.environ.get("DISPLAY") or os.environ.get("GDK_BACKEND") != "x11":
        raise SystemExit("gtk_capture_requires_x11")

    # screen.json is an allowlisted *closed* verdict, never the session ZIP.
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report_file = out / "screen.json"
    report = {
        "scenario": "capture-session-gtk-1366",
        "backend": "GTK4/X11",
        "status": "FAIL",
        "checks": {
            "native_window_mapped": False,
            "window_closed_normally": False,
            "session_zip_0600": False,
            "archive_structure_valid": False,
            "startup_and_shutdown_journal": False,
            "zero_external_accounts": True,
        },
        "synthetic_only": True,
    }
    gate = report["checks"]
    state = {"ticks": 0, "closed": False, "timeout_fired": False}
    active_app = [None]
    failure = ["capture_gtk_not_started"]
    original_argv = sys.argv

    def close_after_mapped():
        state["ticks"] += 1
        instance = Gio.Application.get_default()
        if isinstance(instance, Gtk.Application):
            active_app[0] = instance
            windows = instance.get_windows()
            for window in windows:
                if (window.get_visible() and window.get_mapped()
                        and window.get_native() is not None
                        and window.get_surface() is not None):
                    gate["native_window_mapped"] = True
                    # Queue an actual GTK close request after the visible
                    # frame's main-loop iteration, not a synthetic signal.
                    GLib.idle_add(window.close)
                    state["closed"] = True
                    failure[0] = "capture_gtk_not_closed"
                    return False
        if state["ticks"] >= 200:  # bounded poll: 20 seconds
            failure[0] = "capture_gtk_window_timeout"
            if active_app[0] is not None:
                active_app[0].quit()
            return False
        return True

    def hard_timeout():
        state["timeout_fired"] = True
        if not state["closed"]:
            failure[0] = "capture_gtk_window_timeout"
        elif failure[0] == "capture_gtk_not_closed":
            failure[0] = "capture_gtk_shutdown_timeout"
        if active_app[0] is not None:
            active_app[0].quit()
        return False

    try:
        with tempfile.TemporaryDirectory(prefix="cifereca-capture-gtk-") as private_dir:
            # Private files (including raw logs/cookies) never enter out/.
            GLib.timeout_add(100, close_after_mapped)
            timeout_id = GLib.timeout_add_seconds(30, hard_timeout)
            sys.argv = [
                original_argv[0], "--capture", "--capture-dir", private_dir,
                "--no-plugins",
            ]
            try:
                rc = app.main()  # REAL Gtk.Application startup and shutdown
            finally:
                if not state["timeout_fired"]:
                    GLib.source_remove(timeout_id)
                sys.argv = original_argv
            if (state["timeout_fired"] or not gate["native_window_mapped"]
                    or not state["closed"]):
                failure[0] = "capture_gtk_window_timeout"
            elif rc != 0:
                failure[0] = "capture_gtk_app_error"
            else:
                gate["window_closed_normally"] = True
                files = list(Path(private_dir).glob("cifereca-capture-*.zip"))
                if len(files) != 1:
                    failure[0] = "capture_gtk_zip_count"
                else:
                    archive_path = files[0]
                    gate["session_zip_0600"] = (
                        stat.S_IMODE(archive_path.stat().st_mode) == 0o600
                    )
                    if not gate["session_zip_0600"]:
                        failure[0] = "capture_gtk_zip_permissions"
                    else:
                        verdict = validate_capture_session(archive_path)
                        gate["archive_structure_valid"] = verdict["status"] == "valid"
                        if not gate["archive_structure_valid"]:
                            failure[0] = "capture_gtk_zip_invalid"
                        else:
                            with zipfile.ZipFile(archive_path) as z:
                                manifest = json.loads(z.read("manifest.json"))
                                raw = z.read("logs/journal.jsonl").decode("utf-8")
                            gate["startup_and_shutdown_journal"] = (
                                manifest["app_exit_code"] == 0
                                and manifest["journal_records"] > 0
                                and "TRACE startup" in raw
                                and "TRACE shutdown app.run end" in raw
                            )
                            failure[0] = (
                                "capture_gtk_journal_mismatch"
                                if not gate["startup_and_shutdown_journal"]
                                else "none"
                            )
    except Exception:
        # Never write exception text or ZIP bytes into CI-visible reports.
        failure[0] = "capture_gtk_exception"
    finally:
        report["status"] = "PASS" if all(gate.values()) else "FAIL"
        if report["status"] != "PASS":
            report["failure_code"] = failure[0]
        report_file.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
