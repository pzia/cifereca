#!/usr/bin/env python3
"""P1 #5178: real GTK4/Adw + TaskRunner during synthetic Settings navigation.

Only local exceptions and three concurrent workers. No provider session,
network, authentication, mutable storage or real user account.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
from types import SimpleNamespace as NS

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from cifereca.gui.feedback_dialog_controller import show_error
from cifereca.gui.task_runner import TaskRunner
from cifereca.locking import LockBusyError


PRIVATE_SENTINEL = "PRIVATE_P1_5178_NEVER_EXPORT"


class BusySettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_default_size(850, 580)
        self.set_title("CIFERECA — recette de contention")
        self._closed = False
        self.events = []
        self.receipts = []
        self.toasts = []
        self.diagnostics_rows = []
        self.diagnostics = NS(
            add=lambda *a, **kw: self.diagnostics_rows.append((a, kw))
        )
        self.service = NS(current_identity=None)
        self._pages = {"settings": object(), "library": object()}
        self.view_stack = Gtk.Stack()
        self.status_label = Gtk.Label(label="Aucune tâche exécutée")
        settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        settings.append(Gtk.Label(label="Paramètres — tâches en arrière-plan"))
        settings.append(self.status_label)
        self.view_stack.add_named(Gtk.Label(label="Bibliothèque"), "library")
        self.view_stack.add_named(settings, "settings")
        self.view_stack.set_visible_child_name("library")
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.view_stack)
        self.set_content(self.toast_overlay)
        self.runner = TaskRunner(self, max_workers=3)

    def _functional_error_message(self, message):
        return message

    def toast(self, message, _duration):
        self.toasts.append(message)
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    def show_error(self, message, title="Action impossible"):
        show_error(self, message, title, Adw=Adw)

    def _task_started(self, task_id, _count, _label, _priority, _kind):
        self.events.append(("start", task_id))
        return False

    def _task_progress_for_task(self, _task_id, _label):
        return False

    def _task_finished(self, task_id, _remaining, _label, success, error, *args):
        self.receipts.append((task_id, success, error))
        self.status_label.set_text(error or "Terminé")
        return False


def _visible_modals():
    return [
        widget for widget in Gtk.Window.list_toplevels()
        if isinstance(widget, Adw.MessageDialog) and widget.get_visible()
    ]


def _receipt_details(window):
    return [
        kwargs["detail"] for args, kwargs in window.diagnostics_rows
        if args == ("UI", "background task contention", "warning")
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", type=int, default=1366)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = args.out / "screen.json"
    app = Adw.Application(
        application_id="org.cifereca.p15178.busytest",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    barrier = threading.Barrier(3)
    result = {"status": "FAIL", "workers": 0, "modals": -1}
    state = {"window": None, "done": False}

    def finish(*, timeout=False):
        if state["done"]:
            return False
        state["done"] = True
        window = state["window"]
        if window is not None:
            details = _receipt_details(window)
            modal_count = len(_visible_modals())
            receipts = list(window.receipts)
            success = (
                not timeout and len(receipts) == 3
                and {row[0] for row in receipts} == {1, 2, 3}
                and all(row[1] is False and row[2] ==
                        "Une autre opération est déjà en cours" for row in receipts)
                and len(details) == 3
                and sorted(row["task_ref"] for row in details) == [1, 2, 3]
                and all(row["code"] == "lock_busy"
                        and row["task_classification"] == "background_task"
                        for row in details)
                and len(window.toasts) == 3
                and modal_count == 0
                and window.view_stack.get_visible_child_name() == "settings"
                and window.status_label.get_text() ==
                    "Une autre opération est déjà en cours"
                and PRIVATE_SENTINEL not in repr(details) + repr(window.toasts)
            )
            result.update({
                "status": "PASS" if success else "FAIL",
                "workers": len(receipts), "modals": modal_count,
                "typed_receipts": len(details),
                "toast_count": len(window.toasts),
                "refs_ok": int(sorted(row.get("task_ref") for row in details) == [1, 2, 3]),
                "feedback_ok": int(all(row[1] is False and row[2] ==
                    "Une autre opération est déjà en cours" for row in receipts)),
                "navigation_ok": int(window.view_stack.get_visible_child_name() == "settings"),
                "label_ok": int(window.status_label.get_text() ==
                    "Une autre opération est déjà en cours"),
                "scope_ok": int(all(row.get("code") == "lock_busy" and
                    row.get("task_classification") == "background_task"
                    for row in details)),
            })
            window.runner.close()
            window._closed = True
            window.close()
        report.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
        app.quit()
        return False

    def on_activate(application):
        window = BusySettingsWindow(application)
        state["window"] = window
        window.present()

        def start():
            # Navigation occurs while the three workers are queued.
            window.view_stack.set_visible_child_name("settings")
            assert window.view_stack.get_visible_child_name() == "settings"

            def lock_busy():
                barrier.wait(timeout=4)
                try:
                    raise BlockingIOError(PRIVATE_SENTINEL)
                except BlockingIOError as cause:
                    raise LockBusyError(PRIVATE_SENTINEL) from cause

            def attempted_dialog(_error):
                window.show_error("Traitement indisponible", "Action impossible")

            for i in range(3):
                window.runner.submit(
                    f"worker {i}", lock_busy, kind="maintenance",
                    origin="maintenance", err=attempted_dialog,
                )
            return False

        def poll():
            if len(window.receipts) == 3:
                GLib.timeout_add(150, finish)
                return False
            return True

        GLib.idle_add(start)
        GLib.timeout_add(40, poll)
        GLib.timeout_add(8500, lambda: finish(timeout=True))

    app.connect("activate", on_activate)
    app.run([])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
