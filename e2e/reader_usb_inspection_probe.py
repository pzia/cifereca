#!/usr/bin/env python3
"""P1 #6077: real GTK4 widget contract for an already-associated USB reader.

The GTK Button's clicked signal is invoked programmatically on the GTK
main thread. This is NOT an XTEST pointer/keyboard or physical USB test.
All Linux discoveries, attestations and Core scans are synthetic typed facts.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from time import monotonic, sleep, time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk
from PIL import ImageGrab

from cifereca.core import device_usb_discovery as discovery
from cifereca.core import device_usb_mount as mount
from cifereca.core import device_usb_dry_run as dry_run
from cifereca.gui.reader_usb_preview_actions import request_reader_usb_preview


IDENTITY = "synthetic-identity"
DEVICE = "synthetic-reader"
UUID = "AAAA-0001"
MOUNT = Path("/mnt/typed-synthetic/reader")


class Bindings:
    def __init__(self):
        self.row = {
            "identity_id": IDENTITY, "device_id": DEVICE, "label": "SanDisk",
            "enabled": True, "metadata": {"usb_fingerprint": UUID},
        }

    def device_binding_get(self, device_id):
        return dict(self.row) if device_id == DEVICE else None

    def device_bindings(self):
        return [dict(self.row)]


class RecordingRunner:
    def __init__(self):
        self.jobs = []

    def submit(self, label, work, *, ok, err, control, **options):
        self.jobs.append((work, ok, err, control, options))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", required=True, type=int, choices=(1024, 1366))
    args = parser.parse_args()
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("GTK4 X11 fixture required")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "scenario": f"reader-usb-inspection-{args.width}",
        "status": "RUNNING",
        "backend": "GTK4/Xvfb",
        "activation": "programmatic Gtk.Button clicked signal, not XTEST",
        "width": args.width,
        "checks": {},
        "usb_reads": 0, "usb_writes": 0, "sqlite_writes": 0,
        "provider_requests": 0, "transfers": 0,
    }
    original_discover = discovery.discover_linux_usb_volumes
    original_observe = mount.observe_linux_usb_mount
    original_inspect = dry_run.inspect_usb_dry_run
    state = {"discoveries": 0, "attestations": 0, "scans": 0, "clicked": 0}
    observed = discovery.MountedUsbVolume(
        usb_fingerprint=UUID, mountpoint=MOUNT, filesystem="exfat",
        label="SanDisk", capacity_bytes=500_000_000_000,
        major=8, minor=17, mount_id=189,
    )

    def discover():
        state["discoveries"] += 1
        return (observed,)

    def attest(**kwargs):
        state["attestations"] += 1
        assert kwargs["identity_id"] == IDENTITY
        assert kwargs["mountpoint"] == MOUNT
        return mount.LinuxUsbMount(
            identity_id=IDENTITY, device_id=DEVICE,
            usb_fingerprint=UUID, mountpoint=MOUNT,
            filesystem="exfat", major=8, minor=17,
            checked_at=int(time()), mount_id=189,
        )

    def inspect(**kwargs):
        state["scans"] += 1
        assert kwargs["identity_id"] == IDENTITY
        assert kwargs["device_id"] == DEVICE
        assert kwargs["mountpoint"] == MOUNT
        return dry_run.UsbDryRun(
            identity_id=IDENTITY, device_id=DEVICE,
            status="scanned", scan_attested=True,
            observed_at=int(time()), rows=(),
        )

    discovery.discover_linux_usb_volumes = discover
    mount.observe_linux_usb_mount = attest
    dry_run.inspect_usb_dry_run = inspect
    Gtk.init()
    window = Gtk.Window(title="Cifereca — examen USB synthétique")
    window.set_default_size(args.width - 80, 650)
    storage, runner = Bindings(), RecordingRunner()
    window.service = NS(current_identity=NS(identity_id=IDENTITY), storage=storage)
    window.config = NS(
        identities=lambda: [{"id": IDENTITY, "directory_name": "synthetic"}],
        get=lambda key, default=None: (
            "/tmp/cifereca-reader-inspection-fixture"
            if key == "library.directory" else default
        ),
    )
    window.runner = runner
    window._closed = False
    window._reader_selected_device_id = DEVICE
    window._reader_scope_identity = IDENTITY
    window._identity_context_generation = 3
    window._account_context_generation = 2
    window._route_display_token = object()
    window._reader_selection_generation = 1
    window._reader_library_snapshot = None
    window._reader_status_label = Gtk.Label(label="Non examiné", xalign=0)
    button = Gtk.Button(label="Examiner les changements")
    def clicked(widget):
        state["clicked"] += 1
        request_reader_usb_preview(window, widget, Gtk=Gtk)
    button.connect("clicked", clicked)
    layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    layout.set_margin_top(25)
    layout.set_margin_start(25)
    layout.set_margin_end(25)
    layout.append(Gtk.Label(label="Liseuse USB associée · SanDisk", xalign=0))
    layout.append(button)
    layout.append(window._reader_status_label)
    window.set_child(layout)
    window.present()

    def pump():
        context = GLib.MainContext.default()
        context.iteration(False)
        while context.pending():
            context.iteration(False)

    def until_mapped():
        limit = monotonic() + 6
        while monotonic() < limit:
            pump()
            if window.get_mapped() and button.get_mapped():
                return
            sleep(.025)
        raise AssertionError("real GTK window or button not mapped")

    try:
        until_mapped()
        assert len(runner.jobs) == 0
        assert state["discoveries"] == 0
        assert getattr(window, "_reader_usb_preview_chooser", None) is None
        report["checks"]["no_implicit_usb_search_on_render"] = True

        # A real Gtk.Button and connected production handler; deliberate
        # programmatic clicked signal, never claimed as native user input.
        button.emit("clicked")
        assert state["clicked"] == 1
        assert len(runner.jobs) == 1
        assert "Recherche du volume" in window._reader_status_label.get_text()
        assert getattr(window, "_reader_usb_preview_chooser", None) is None
        report["checks"]["real_gtk_button_queues_only_discovery"] = True

        work, ok, err, control, options = runner.jobs[0]
        assert control is button and options["origin"] == "user"
        chosen = work()
        assert chosen == observed
        assert state["discoveries"] == state["attestations"] == 1
        ok(chosen)
        pump()
        assert len(runner.jobs) == 2
        assert getattr(window, "_reader_usb_preview_chooser", None) is None
        report["checks"]["exact_mount_skips_native_folder_chooser"] = True

        scan, done, failed, control, options = runner.jobs[1]
        assert control is button
        assert state["scans"] == 0
        result = scan()
        assert state["discoveries"] == state["attestations"] == 2
        assert state["scans"] == 1
        done(result)
        pump()
        assert "Aperçu USB ponctuel" in window._reader_status_label.get_text()
        assert getattr(window, "_reader_usb_preview_chooser", None) is None
        report["checks"]["visible_scoped_gtk_report_without_usb_io"] = True

        screenshot = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert screenshot.size == (args.width, 900)
        picture_path = out / "screen.png"
        screenshot.save(picture_path)
        report["screen_sha256"] = sha256(picture_path.read_bytes()).hexdigest()

        window.service.current_identity = NS(identity_id="another-profile")
        button.emit("clicked")
        assert len(runner.jobs) == 2 and state["scans"] == 1
        report["checks"]["revoked_profile_cannot_start_new_gtk_scan"] = True
        report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "FAIL"
        report["error_code"] = type(exc).__name__
    finally:
        discovery.discover_linux_usb_volumes = original_discover
        mount.observe_linux_usb_mount = original_observe
        dry_run.inspect_usb_dry_run = original_inspect
        window._closed = True
        window.destroy()
        report["synthetic_discovery_reads"] = state["discoveries"]
        report["synthetic_mount_attestations"] = state["attestations"]
        report["synthetic_core_scans"] = state["scans"]
        (out / "screen.json").write_text(
            json.dumps(report, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
