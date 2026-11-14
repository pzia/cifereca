#!/usr/bin/env python3
"""P1 #6041: real GTK4/Adw USB pairing with XTEST, no physical USB access.

The product's request_reader_pairing function builds real GTK widgets, with
synthetic already-qualified MountedUsbVolume observations.  This is a dialog
acceptance test, NOT an attestation of a real reader, USB mount or copy.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from time import monotonic, sleep
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "environnements/openai-chat-debian")]

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk
from PIL import ImageGrab

from cifereca.core import device_usb_discovery as discovery
from cifereca.gui.reader_pairing_actions import request_reader_pairing
from x11_input import X11Input


class MemoryBindings:
    """Observes declarative binding calls; never touches disks, SQLite or USB."""
    def __init__(self):
        self.rows = []
        self.writes = 0

    def device_bindings(self):
        return [dict(row) for row in self.rows]

    def device_usb_binding_declare(self, *, identity_id, device_id, label,
                                   usb_fingerprint, enabled):
        self.writes += 1
        row = dict(identity_id=identity_id, device_id=device_id, label=label,
                   enabled=enabled, metadata={"usb_fingerprint": usb_fingerprint})
        self.rows[:] = [old for old in self.rows if old["device_id"] != device_id]
        self.rows.append(row)


def volume(label, uuid, mount, minor, mount_id):
    return discovery.MountedUsbVolume(
        usb_fingerprint=uuid, mountpoint=Path("/mnt/synthetic/" + mount),
        filesystem="exfat" if minor == 17 else "ext4",
        label=label, capacity_bytes=256_000_000_000,
        major=8, minor=minor, mount_id=mount_id,
    )


def children(widget):
    result = []
    child = widget.get_first_child()
    while child is not None:
        result.append(child)
        child = child.get_next_sibling()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1024, 1366), required=True)
    args = parser.parse_args()
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("GTK4 X11/XTEST runtime required")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "scenario": f"reader-usb-pairing-{args.width}",
        "status": "RUNNING", "backend": "GTK4/X11/XTEST",
        "width": args.width, "phase": "setup", "checks": {},
        "synthetic_volume_reads": 0, "declarative_binding_writes": 0,
        "usb_file_writes": 0, "provider_requests": 0, "transfers": 0,
    }

    def save():
        (out / "screen.json").write_text(
            json.dumps(report, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

    Gtk.init()
    Adw.init()
    device = X11Input()
    storage = MemoryBindings()
    state = {"volumes": (), "reads": 0, "errors": 0, "renders": 0}
    original_discovery = discovery.discover_linux_usb_volumes

    def discover():
        state["reads"] += 1
        return tuple(state["volumes"])

    discovery.discover_linux_usb_volumes = discover

    window = Gtk.Window(title="Cifereca — recette USB synthétique")
    window.set_default_size(args.width - 130, 700)
    window.service = NS(current_identity=NS(identity_id="alice"), storage=storage)
    window._reader_scope_identity = "alice"
    window._identity_context_generation = 0
    window._account_context_generation = 0
    window._route_display_token = object()
    window._closed = False
    window._reader_status_label = Gtk.Label(label="Aucune association")
    window._render_readers = lambda: state.__setitem__(
        "renders", state["renders"] + 1,
    )
    window.show_error = lambda *_: state.__setitem__(
        "errors", state["errors"] + 1,
    )

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for fn in (box.set_margin_top, box.set_margin_start, box.set_margin_end):
        fn(22)
    heading = Gtk.Label(label="Liseuses — association USB (simulation sans appareil)")
    box.append(heading)
    button = Gtk.Button(label="Associer une liseuse")
    button.connect("clicked", lambda *_: request_reader_pairing(
        window, Gtk=Gtk, Adw=Adw,
    ))
    box.append(button)
    box.append(window._reader_status_label)
    window.set_child(box)
    window.present()

    def pump():
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)

    def until(predicate, key, seconds=6):
        limit = monotonic() + seconds
        while monotonic() < limit:
            pump()
            if predicate():
                return
            sleep(0.025)
        raise AssertionError("gtk_" + key + "_not_observed")

    def click(widget):
        until(lambda: widget.get_mapped(), "widget_mapped")
        device.click_widget(widget)
        pump()

    def open_dialog():
        click(button)  # Real XTEST click, real product callback, real Adw modal.
        until(lambda: getattr(window, "_reader_pairing_dialog", None) is not None
              and window._reader_pairing_dialog.get_visible(), "dialog_open")
        dialog = window._reader_pairing_dialog
        parts = children(dialog.get_extra_child())
        assert len(parts) == 10
        label, chooser, refresh, status, _name_label, name, expert, uuid_label, uuid, notice = parts
        assert isinstance(chooser, Gtk.DropDown)
        assert isinstance(expert, Gtk.CheckButton)
        assert isinstance(uuid, Gtk.Entry)
        assert not expert.get_active() and not uuid.get_visible()
        assert chooser.get_selected() == 0xFFFFFFFF  # never auto-selected
        return dialog, label, chooser, refresh, status, name, expert, uuid, notice

    def answer(dialog, response):
        dialog.response(response)
        until(lambda: window._reader_pairing_dialog is None, "dialog_response")
        pump()

    def capture():
        picture = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert picture.size == (args.width, 900)
        png = out / "screen.png"
        picture.save(png)
        report["screen_sha256"] = sha256(png.read_bytes()).hexdigest()

    try:
        until(lambda: window.get_mapped() and button.get_mapped(), "shell")
        # Zero discovered USB volumes: explicit expert path, no fake selection.
        report["phase"] = "zero_usb_expert"
        state["volumes"] = ()
        dialog, label, chooser, refresh, status, name, expert, uuid, notice = open_dialog()
        assert chooser.get_sensitive() is False
        assert "Aucun volume" in status.get_text()
        assert chooser.get_model().get_n_items() == 1
        assert not uuid.get_visible() and label.get_visible()
        click(expert)  # Actual GTK/XTEST click into the expert checkbutton.
        until(lambda: expert.get_active() and uuid.get_visible()
              and not chooser.get_visible() and not label.get_visible(),
              "expert_mode")
        assert notice.get_visible()
        click(expert)
        until(lambda: not expert.get_active() and chooser.get_visible()
              and label.get_visible() and not uuid.get_visible(),
              "chooser_return")
        answer(dialog, "cancel")
        assert storage.writes == 0 and state["errors"] == 0
        report["checks"]["zero_discovery_and_expert_toggle"] = True

        # One mounted volume; no pre-selection, no binding on mere opening.
        report["phase"] = "one_usb"
        sandisk = volume("SanDisk", "AAAA-1111", "sandisk", 17, 189)
        state["volumes"] = (sandisk,)
        dialog, label, chooser, refresh, status, name, expert, uuid, notice = open_dialog()
        assert chooser.get_sensitive() and chooser.get_selected() == 0xFFFFFFFF
        assert chooser.get_model().get_n_items() == 1
        display = chooser.get_model().get_string(0)
        assert "SanDisk" in display and "exFAT" in display
        assert "AAAA-1111" not in display
        assert storage.writes == 0
        capture()
        # Confirm with no choice: fail closed, never select the first volume.
        answer(dialog, "confirm")
        assert state["errors"] == 1 and storage.writes == 0
        report["checks"]["single_device_not_autoselected"] = True

        dialog, label, chooser, refresh, status, name, expert, uuid, notice = open_dialog()
        chooser.set_selected(0)  # Real Gtk.DropDown notify handler, exact choice.
        assert name.get_text() == "SanDisk"
        name.set_text("Ma liseuse")
        before = state["reads"]
        answer(dialog, "confirm")
        assert state["reads"] == before + 1  # fresh re-observation at confirm
        assert storage.writes == 1 and storage.rows[0]["label"] == "Ma liseuse"
        assert storage.rows[0]["metadata"]["usb_fingerprint"] == "AAAA-1111"
        assert storage.rows[0]["enabled"] is False
        assert state["renders"] == 1
        report["checks"]["friendly_choice_reobserved_and_disabled"] = True

        # N volumes: explicit second choice, unplug/replug race rejected.
        report["phase"] = "multiple_usb_and_replug"
        second = volume("Reader B", "BBBB-2222", "reader-b", 18, 202)
        state["volumes"] = (sandisk, second)
        dialog, label, chooser, refresh, status, name, expert, uuid, notice = open_dialog()
        assert chooser.get_model().get_n_items() == 2
        assert chooser.get_selected() == 0xFFFFFFFF
        chooser.set_selected(1)
        assert name.get_text() == "Reader B"
        # Mount ID changed after selection, despite same mount path and UUID.
        state["volumes"] = (
            sandisk, volume("Reader B", "BBBB-2222", "reader-b", 18, 203),
        )
        answer(dialog, "confirm")
        assert state["errors"] == 2 and storage.writes == 1
        report["checks"]["changed_mount_id_refused"] = True

        dialog, label, chooser, refresh, status, name, expert, uuid, notice = open_dialog()
        chooser.set_selected(1)
        assert name.get_text() == "Reader B"
        answer(dialog, "confirm")
        assert storage.writes == 2
        assert {row["metadata"]["usb_fingerprint"] for row in storage.rows} == {
            "AAAA-1111", "BBBB-2222",
        }
        assert all(row["enabled"] is False for row in storage.rows)
        report["checks"]["multiple_devices_explicit_second_choice"] = True

        # Changing the selected identity invalidates the modal on confirm.
        report["phase"] = "identity_revocation"
        dialog, label, chooser, refresh, status, name, expert, uuid, notice = open_dialog()
        chooser.set_selected(0)
        window.service.current_identity = NS(identity_id="bob")
        answer(dialog, "confirm")
        assert storage.writes == 2 and state["errors"] == 2
        report["checks"]["profile_changed_refuses_write"] = True

        # Existing declarative UUID still works in explicit expert mode.
        report["phase"] = "manual_expert"
        window.service.current_identity = NS(identity_id="alice")
        state["volumes"] = ()
        dialog, label, chooser, refresh, status, name, expert, uuid, notice = open_dialog()
        click(expert)
        until(lambda: expert.get_active() and uuid.get_visible(), "manual_uuid")
        name.set_text("Lecteur expert")
        uuid.set_text("CCCC-3333")
        before = state["reads"]
        answer(dialog, "confirm")
        assert state["reads"] == before  # expert path makes no USB claim
        assert storage.writes == 3 and storage.rows[-1]["enabled"] is False
        assert storage.rows[-1]["metadata"]["usb_fingerprint"] == "CCCC-3333"
        report["checks"]["manual_uuid_remains_explicit_declarative"] = True

        report["checks"]["no_physical_usb_or_provider_io"] = True
        report.update(
            status="PASS", synthetic_volume_reads=state["reads"],
            declarative_binding_writes=storage.writes,
            checks={key: bool(value) for key, value in report["checks"].items()},
        )
    except Exception as exc:
        # Never print upstream paths, raw UUIDs or provider exceptions.
        report.update(status="FAIL", error_code=type(exc).__name__)
    finally:
        discovery.discover_linux_usb_volumes = original_discovery
        window._closed = True
        window.destroy()
        device.close()
        save()
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
