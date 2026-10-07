from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from gi.repository import GLib, Gtk

from cifereca.plugin_api import ArtifactFormat, ArtifactRef
from cifereca_epub_optimizer.ui import OptimizerPage


class ProfileService:
    def describe(self):
        return {
            "id": "xteink-x3",
            "label": "Xteink X3 / CrossPoint (528×792, 4 gris)",
            "width": 528,
            "height": 792,
            "max_zoom_factor": None,
            "allow_landscape": False,
            "grayscale": True,
            "gray_levels": [0, 85, 170, 255],
            "autocontrast_cutoff": 1.0,
            "contrast_factor": 1.5,
            "jpeg_quality": 70,
            "suffix": "X3",
            "description": "Profil e-ink de recette",
        }


class FakeControl:
    def __init__(self, on_progress=None):
        self._cancelled = threading.Event()
        self._on_progress = on_progress

    def cancel(self):
        self._cancelled.set()

    def cancelled(self):
        return self._cancelled.is_set()

    def check_cancelled(self):
        if self.cancelled():
            raise RuntimeError("cancelled")

    def report(self, *, current=None, total=None, message=""):
        if self._on_progress is not None and not self.cancelled():
            self._on_progress(current, total, str(message or ""))


class FakeTaskHandle:
    def __init__(self, future: Future, control: FakeControl):
        self._future = future
        self.control = control

    def cancel(self):
        self.control.cancel()
        self._future.cancel()

    def done(self):
        return self._future.done()

    def result(self, timeout=None):
        return self._future.result(timeout=timeout)

    def add_done_callback(self, callback):
        self._future.add_done_callback(lambda _future: callback(self))


class Tasks:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="optimizer-e2e")

    def submit(self, operation, *, priority=40, label="", kind="plugin", on_progress=None):
        del priority, label, kind
        control = FakeControl(on_progress)
        future = self._executor.submit(operation, control)
        return FakeTaskHandle(future, control)

    def close(self):
        self._executor.shutdown(wait=False, cancel_futures=True)


class Execution:
    def __init__(self, state_prefix: Path):
        self.calls = []
        self.state_prefix = state_prefix

    def _mark(self, name: str):
        marker = Path(str(self.state_prefix) + f"-{name}.ready")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("ready\n", encoding="utf-8")

    def execute_transform(self, request, *, transformer_plugin_id="", control=None):
        self.calls.append((request.capability_id, transformer_plugin_id, dict(request.options)))
        control.report(current=10, total=100, message="Analyse")
        time.sleep(0.35)
        control.report(current=55, total=100, message="Optimisation des images")
        time.sleep(0.20)
        self._mark("progress")
        time.sleep(0.50)
        control.report(current=90, total=100, message="Validation")
        time.sleep(0.35)
        fmt = request.source.format
        return ArtifactRef(
            "sha256:" + "1" * 64,
            "book-1",
            fmt,
            99,
            "1" * 64,
            variant_kind="optimized",
            filename=f"Livre [X3].{fmt.format}",
        )


class Host:
    def __init__(self):
        self.toasts = []
    def toast(self, message):
        self.toasts.append(str(message))


class ActionHost:
    def __init__(self):
        self.refreshes = 0
    def refresh_context(self):
        self.refreshes += 1


class Context:
    def __init__(self, execution, tasks):
        self.config = {
            "default_profile": "device.profile.xteink-x3",
            "max_uncompressed_mb": 512,
            "max_pdf_pages": 1000,
            "max_image_pixels": 20_000_000,
            "max_image_mb": 64,
        }
        self.logger = SimpleNamespace(
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            info=lambda *args, **kwargs: None,
        )
        self._execution = execution
        self._tasks = tasks
        self._row = SimpleNamespace(
            service=ProfileService(),
            plugin_id="device.profile.xteink-x3",
            plugin_version="0.3.0",
        )

    def services(self, category, **kwargs):
        assert category == "device.profile"
        return (self._row,)

    def subscribe_services(self, category, callback):
        assert category == "device.profile"
        return lambda: None

    def broker(self, name):
        if name == "artifact.execution":
            return self._execution
        if name == "tasks":
            return self._tasks
        raise AssertionError(name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("epub", "pdf"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--state-prefix", type=Path, required=True)
    args = parser.parse_args()

    fmt = args.format
    media = "application/pdf" if fmt == "pdf" else "application/epub+zip"
    artifact = ArtifactRef(
        "sha256:" + "0" * 64,
        "book-1",
        ArtifactFormat(media, fmt, "none"),
        123,
        "0" * 64,
        filename=f"Livre.{fmt}",
    )

    app = Gtk.Application(application_id=f"org.cifereca.optimizer.e2e.{fmt}")
    execution = Execution(args.state_prefix)
    tasks = Tasks()
    host = Host()
    action_host = ActionHost()
    state = {"heartbeats": 0, "started": False, "controller": None}

    def activate(application):
        controller = OptimizerPage(
            host=host,
            context=Context(execution, tasks),
            artifact=artifact,
            action_host=action_host,
        )
        state["controller"] = controller
        window = Gtk.ApplicationWindow(application=application)
        window.set_title(f"Cifereca optimizer E2E — {fmt.upper()}")
        window.set_default_size(1000, 700)
        window.set_child(controller.widget)
        window.present()

        def heartbeat():
            state["heartbeats"] += 1
            return True

        def start():
            state["started"] = True
            controller.optimize_button.emit("clicked")
            return False

        def check_finished():
            ctl = state["controller"]
            if ctl.progress.get_fraction() < 1.0 or "Variante disponible" not in ctl.status.get_text():
                return True
            expected_capability = f"{fmt}.image-optimize-eink"
            expected_transformer = (
                "document.transformer.pdf-eink-optimizer"
                if fmt == "pdf"
                else "document.transformer.epub-optimizer"
            )
            report = {
                "format": fmt,
                "heartbeats": state["heartbeats"],
                "started": state["started"],
                "calls": execution.calls,
                "progress_text": ctl.progress.get_text(),
                "progress_fraction": ctl.progress.get_fraction(),
                "status": ctl.status.get_text(),
                "toasts": host.toasts,
                "refreshes": action_host.refreshes,
                "source_sensitive": ctl.source_entry.get_sensitive(),
                "output_sensitive": ctl.output_entry.get_sensitive(),
                "cancel_sensitive": ctl.cancel_button.get_sensitive(),
                "profile": ctl.profile_combo.get_active_id(),
                "profile_help": ctl.profile_help.get_text(),
            }
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            assert state["heartbeats"] >= 15, report
            assert execution.calls and execution.calls[0][0] == expected_capability, report
            assert execution.calls[0][1] == expected_transformer, report
            assert ctl.progress.get_fraction() == 1.0, report
            assert "Variante disponible" in ctl.status.get_text(), report
            assert action_host.refreshes == 1, report
            assert not ctl.source_entry.get_sensitive(), report
            assert not ctl.output_entry.get_sensitive(), report
            final_marker = Path(str(args.state_prefix) + "-final.ready")
            final_marker.write_text("ready\n", encoding="utf-8")

            def quit_later():
                tasks.close()
                application.quit()
                return False

            GLib.timeout_add(600, quit_later)
            return False

        GLib.timeout_add(50, heartbeat)
        GLib.timeout_add(400, start)
        GLib.timeout_add(50, check_finished)

    app.connect("activate", activate)
    raise SystemExit(app.run([]))


if __name__ == "__main__":
    main()
