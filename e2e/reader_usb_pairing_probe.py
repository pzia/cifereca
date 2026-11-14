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
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
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
    # Give XTEST a GApplication-owned, active GTK native surface. A
    # standalone window can appear mapped under Xvfb yet reject input.
    app = Adw.Application(
        application_id="org.cifereca.p16041.readerpairing",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    device = X11Input()
    storage = MemoryBindings()
    state = {
        "volumes": (), "reads": 0, "errors": 0, "renders": 0,
        "button_clicks": 0, "dialog_build_errors": 0,
        "checkpoint": "setup", "pointer_seen": 0, "key_seen": 0,
        "native_pointer_seen": 0, "native_key_seen": 0,
    }
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
    def on_pair_clicked(*_args):
        state["button_clicks"] += 1
        try:
            request_reader_pairing(window, Gtk=Gtk, Adw=Adw)
        except Exception:
            # Keep raw GTK/GObject exception text inside the runner, not CI.
            state["dialog_build_errors"] += 1
    button.connect("clicked", on_pair_clicked)
    # Diagnostics only: observe actual GDK delivery, never synthesize GTK events.
    pointer_observer = Gtk.EventControllerLegacy()
    def observe_pointer(_controller, event):
        if event.get_event_type() == Gdk.EventType.BUTTON_PRESS:
            state["pointer_seen"] += 1
        return False  # observe; never claim the GTK button's click sequence
    pointer_observer.connect("event", observe_pointer)
    button.add_controller(pointer_observer)
    key_observer = Gtk.EventControllerKey()
    key_observer.connect(
        "key-pressed", lambda *_: (
            state.__setitem__("key_seen", state["key_seen"] + 1), False,
        )[-1],
    )
    button.add_controller(key_observer)
    # Capture native GDK delivery on the toplevel as well as on the button.
    # These observers never activate widgets or intercept their events.
    native_pointer = Gtk.EventControllerLegacy()
    native_pointer.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    def observe_native_pointer(_controller, event):
        if event.get_event_type() == Gdk.EventType.BUTTON_PRESS:
            state["native_pointer_seen"] += 1
        return False
    native_pointer.connect("event", observe_native_pointer)
    window.add_controller(native_pointer)
    native_key = Gtk.EventControllerKey()
    native_key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    native_key.connect(
        "key-pressed", lambda *_: (
            state.__setitem__("native_key_seen", state["native_key_seen"] + 1),
            False,
        )[-1],
    )
    window.add_controller(native_key)
    box.append(button)
    box.append(window._reader_status_label)
    window.set_child(box)
    def until(predicate, key, seconds=6):
        # A GTK idle callback must return to the *outer* GApplication loop:
        # nested MainContext.iteration() starves native GDK/X11 event delivery.
        state["checkpoint"] = key
        limit = monotonic() + seconds
        while monotonic() < limit:
            if predicate():
                return
            yield 25
        raise AssertionError("gtk_" + key + "_not_observed")

    def focus_native(widget):
        # Xvfb has no window manager to grant keyboard input focus on click.
        # The X11 surface belongs to the REAL Gtk.Window/Adw.MessageDialog.
        # This only focuses the window: all activations still use XTEST.
        import ctypes as C
        from native_capture import pointer

        native = widget.get_native()
        if native is None or native.get_surface() is None:
            raise RuntimeError("GTK native surface unavailable")
        gtk_lib = C.CDLL("libgtk-4.so.1")
        gtk_lib.gdk_x11_surface_get_xid.argtypes = [C.c_void_p]
        gtk_lib.gdk_x11_surface_get_xid.restype = C.c_ulong
        xid = gtk_lib.gdk_x11_surface_get_xid(
            pointer(native.get_surface()),
        )
        if not xid:
            raise RuntimeError("X11 native surface unavailable")
        device.x11.XSetInputFocus.argtypes = [
            C.c_void_p, C.c_ulong, C.c_int, C.c_ulong,
        ]
        device.x11.XSetInputFocus.restype = C.c_int
        device.x11.XSetInputFocus(device.display, xid, 1, 0)
        device.x11.XSync(device.display, False)
        widget.grab_focus()
        # Let the normal GTK event loop dispatch native X11 focus changes.
        yield 25

    def delivered(predicate, *, seconds=1.2):
        # Real XTEST events require the outer GTK loop to resume before
        # their controller/button callbacks can run. Never pump recursively.
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            if predicate():
                return True
            yield 25
        return False

    def click(widget):
        yield from until(lambda: widget.get_mapped(), "widget_mapped")
        # A mapped button need not occupy the screen coordinates predicted by
        # a GTK4 native-surface transform (especially with Xvfb CSD). Compute
        # an actual native-local GTK hit and translate THAT position to XTEST.
        # Never bypass the GTK input pipeline with emit()/activate()/clicked().
        native = widget.get_native()
        success, bounds = widget.compute_bounds(native)
        if not success:
            state["checkpoint"] = "widget_geometry"
            raise AssertionError("GTK widget has no native bounds")
        local_x = bounds.get_x() + bounds.get_width() / 2
        local_y = bounds.get_y() + bounds.get_height() / 2
        hit = native.pick(local_x, local_y, Gtk.PickFlags.DEFAULT)
        while hit is not None and hit is not widget:
            hit = hit.get_parent()
        state["geometry_hit"] = hit is widget
        if not state["geometry_hit"]:
            state["checkpoint"] = "widget_geometry"
            raise AssertionError("GTK native hit-test misses the intended control")
        screen_x, screen_y = device.window_point(native, local_x, local_y)
        state["geometry_onscreen"] = (
            0 <= screen_x < args.width and 0 <= screen_y < 900
        )
        if not state["geometry_onscreen"]:
            state["checkpoint"] = "widget_geometry"
            raise AssertionError("XTEST target is outside physical Xvfb screen")
        # Prove that XTEST's physical cursor is over this native X11
        # surface, not merely inside GTK's *logical* hit test. A compositor
        # or another toplevel may obscure a geometrically valid target.
        import ctypes as C
        from native_capture import pointer
        gtk = C.CDLL("libgtk-4.so.1")
        gtk.gdk_x11_surface_get_xid.argtypes = [C.c_void_p]
        gtk.gdk_x11_surface_get_xid.restype = C.c_ulong
        xid = gtk.gdk_x11_surface_get_xid(pointer(native.get_surface()))
        x11 = device.x11
        x11.XDefaultRootWindow.argtypes = [C.c_void_p]
        x11.XDefaultRootWindow.restype = C.c_ulong
        x11.XQueryPointer.argtypes = [
            C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong),
            C.POINTER(C.c_ulong), C.POINTER(C.c_int),
            C.POINTER(C.c_int), C.POINTER(C.c_int),
            C.POINTER(C.c_int), C.POINTER(C.c_uint),
        ]
        x11.XQueryPointer.restype = C.c_int
        root = x11.XDefaultRootWindow(device.display)
        def pointer_targets_native():
            parent, child = C.c_ulong(), C.c_ulong()
            rx, ry, wx, wy, mask = (
                C.c_int(), C.c_int(), C.c_int(), C.c_int(), C.c_uint()
            )
            ok = x11.XQueryPointer(
                device.display, root, C.byref(parent), C.byref(child),
                C.byref(rx), C.byref(ry), C.byref(wx), C.byref(wy),
                C.byref(mask),
            )
            return bool(ok and xid and child.value == xid)
        device.move(screen_x, screen_y)
        if not pointer_targets_native():
            # Only raise this temporary synthetic GTK toplevel within
            # private Xvfb. This cannot reconfigure or act on physical USB.
            x11.XRaiseWindow.argtypes = [C.c_void_p, C.c_ulong]
            x11.XRaiseWindow(device.display, xid)
            x11.XSync(device.display, False)
            device.move(screen_x, screen_y)
        state["x11_target_window"] = pointer_targets_native()
        if not state["x11_target_window"]:
            state["checkpoint"] = "x11_hit_mismatch"
            raise AssertionError("XTEST root hit does not match GTK native")
        # Verify the *physical* X11 pointer translated back into the GDK
        # widget's logical hit-test, rather than checking a predicted GTK
        # point plus a separate top-level window ID.
        qroot, qchild = C.c_ulong(), C.c_ulong()
        qrx, qry, qwx, qwy, qmask = (
            C.c_int(), C.c_int(), C.c_int(), C.c_int(), C.c_uint()
        )
        queried = x11.XQueryPointer(
            device.display, xid, C.byref(qroot), C.byref(qchild),
            C.byref(qrx), C.byref(qry), C.byref(qwx), C.byref(qwy),
            C.byref(qmask),
        )
        surface_tx, surface_ty = native.get_surface_transform()
        scale = int(widget.get_scale_factor())
        logical_x = qwx.value / scale - surface_tx
        logical_y = qwy.value / scale - surface_ty
        native_hit = (
            native.pick(logical_x, logical_y, Gtk.PickFlags.DEFAULT)
            if queried else None
        )
        while native_hit is not None and native_hit is not widget:
            native_hit = native_hit.get_parent()
        state["x11_widget_hit"] = native_hit is widget
        # Keep the event delivery native, identical to all existing passing
        # probes; observe XTEST's actual queue acknowledgement in addition.
        device.move(screen_x, screen_y)
        queued_press = device.xtst.XTestFakeButtonEvent(
            device.display, 1, True, 0,
        )
        queued_release = device.xtst.XTestFakeButtonEvent(
            device.display, 1, False, 0,
        )
        device.x11.XSync(device.display, False)
        state["xtest_button_queued"] = bool(queued_press and queued_release)

    def toggle_expert(widget):
        was_active = widget.get_active()
        yield from click(widget)
        if not (yield from delivered(lambda: widget.get_active() is not was_active)):
            # Pure XTEST keyboard fallback, never set_active or emit clicked.
            yield from focus_native(widget)
            device.key("space", True)
            device.key("space", False)
            yield from until(lambda: widget.get_active() is not was_active,
                  "expert_keyboard")

    def open_dialog():
        before = state["button_clicks"]
        yield from click(button)  # Real XTEST click, real product callback, real Adw modal.
        if not (yield from delivered(lambda: state["button_clicks"] > before)):
            # An unmanaged Xvfb toplevel may consume an initial pointer click
            # for focus only. Retry once, after allowing event dispatch.
            yield from click(button)
        if not (yield from delivered(lambda: state["button_clicks"] > before)):
            # Keyboard XTEST fallback, never call the product handler directly.
            yield from focus_native(button)
            device.key("Return", True)
            device.key("Return", False)
        if not (yield from delivered(lambda: state["button_clicks"] > before)):
            # A native GTK button may accept Space instead of Return.
            yield from focus_native(button)
            device.key("space", True)
            device.key("space", False)
        if not (yield from delivered(lambda: state["button_clicks"] > before)):
            state["checkpoint"] = "button_not_clicked"
            raise AssertionError("GTK/XTEST pairing button did not activate")
        if state["dialog_build_errors"]:
            state["checkpoint"] = "dialog_build_failed"
            raise AssertionError("GTK pairing dialog construction failed")
        yield from until(lambda: getattr(window, "_reader_pairing_dialog", None) is not None
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
        yield from until(lambda: window._reader_pairing_dialog is None, "dialog_response")
        yield 25

    def capture():
        picture = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert picture.size == (args.width, 900)
        png = out / "screen.png"
        picture.save(png)
        report["screen_sha256"] = sha256(png.read_bytes()).hexdigest()

    def run_checks():
        try:
            yield from until(lambda: window.get_mapped() and button.get_mapped(), "shell")
            # Zero discovered USB volumes: explicit expert path, no fake selection.
            report["phase"] = "zero_usb_expert"
            state["volumes"] = ()
            dialog, label, chooser, refresh, status, name, expert, uuid, notice = yield from open_dialog()
            assert chooser.get_sensitive() is False
            assert "Aucun volume" in status.get_text()
            assert chooser.get_model().get_n_items() == 1
            assert not uuid.get_visible() and label.get_visible()
            yield from toggle_expert(expert)  # Actual GTK/XTEST click into the expert checkbutton.
            yield from until(lambda: expert.get_active() and uuid.get_visible()
                  and not chooser.get_visible() and not label.get_visible(),
                  "expert_mode")
            assert notice.get_visible()
            yield from toggle_expert(expert)
            yield from until(lambda: not expert.get_active() and chooser.get_visible()
                  and label.get_visible() and not uuid.get_visible(),
                  "chooser_return")
            yield from answer(dialog, "cancel")
            assert storage.writes == 0 and state["errors"] == 0
            report["checks"]["zero_discovery_and_expert_toggle"] = True

            # One mounted volume; no pre-selection, no binding on mere opening.
            report["phase"] = "one_usb"
            sandisk = volume("SanDisk", "AAAA-1111", "sandisk", 17, 189)
            state["volumes"] = (sandisk,)
            dialog, label, chooser, refresh, status, name, expert, uuid, notice = yield from open_dialog()
            assert chooser.get_sensitive() and chooser.get_selected() == 0xFFFFFFFF
            assert chooser.get_model().get_n_items() == 1
            display = chooser.get_model().get_string(0)
            assert "SanDisk" in display and "exFAT" in display
            assert "AAAA-1111" not in display
            assert storage.writes == 0
            capture()
            # Confirm with no choice: fail closed, never select the first volume.
            yield from answer(dialog, "confirm")
            assert state["errors"] == 1 and storage.writes == 0
            report["checks"]["single_device_not_autoselected"] = True

            dialog, label, chooser, refresh, status, name, expert, uuid, notice = yield from open_dialog()
            chooser.set_selected(0)  # Real Gtk.DropDown notify handler, exact choice.
            assert name.get_text() == "SanDisk"
            name.set_text("Ma liseuse")
            before = state["reads"]
            yield from answer(dialog, "confirm")
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
            dialog, label, chooser, refresh, status, name, expert, uuid, notice = yield from open_dialog()
            assert chooser.get_model().get_n_items() == 2
            assert chooser.get_selected() == 0xFFFFFFFF
            chooser.set_selected(1)
            assert name.get_text() == "Reader B"
            # Mount ID changed after selection, despite same mount path and UUID.
            state["volumes"] = (
                sandisk, volume("Reader B", "BBBB-2222", "reader-b", 18, 203),
            )
            yield from answer(dialog, "confirm")
            assert state["errors"] == 2 and storage.writes == 1
            report["checks"]["changed_mount_id_refused"] = True

            dialog, label, chooser, refresh, status, name, expert, uuid, notice = yield from open_dialog()
            chooser.set_selected(1)
            assert name.get_text() == "Reader B"
            yield from answer(dialog, "confirm")
            assert storage.writes == 2
            assert {row["metadata"]["usb_fingerprint"] for row in storage.rows} == {
                "AAAA-1111", "BBBB-2222",
            }
            assert all(row["enabled"] is False for row in storage.rows)
            report["checks"]["multiple_devices_explicit_second_choice"] = True

            # Changing the selected identity invalidates the modal on confirm.
            report["phase"] = "identity_revocation"
            dialog, label, chooser, refresh, status, name, expert, uuid, notice = yield from open_dialog()
            chooser.set_selected(0)
            window.service.current_identity = NS(identity_id="bob")
            yield from answer(dialog, "confirm")
            assert storage.writes == 2 and state["errors"] == 2
            report["checks"]["profile_changed_refuses_write"] = True

            # Existing declarative UUID still works in explicit expert mode.
            report["phase"] = "manual_expert"
            window.service.current_identity = NS(identity_id="alice")
            state["volumes"] = ()
            dialog, label, chooser, refresh, status, name, expert, uuid, notice = yield from open_dialog()
            yield from toggle_expert(expert)
            yield from until(lambda: expert.get_active() and uuid.get_visible(), "manual_uuid")
            name.set_text("Lecteur expert")
            uuid.set_text("CCCC-3333")
            before = state["reads"]
            yield from answer(dialog, "confirm")
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
            # Only closed checkpoint names may enter CI; no traceback, URL,
            # fixture mountpath, UUID or provider-controlled string.
            report.update(status="FAIL", error_code=type(exc).__name__)
            allowed = {
                "shell", "widget_mapped", "dialog_open", "expert_mode",
                "chooser_return", "dialog_response", "manual_uuid",
                "button_not_clicked", "widget_geometry", "x11_hit_mismatch",
                "dialog_build_failed", "expert_keyboard", "setup",
            }
            checkpoint = state["checkpoint"]
            if checkpoint in allowed:
                print("P1_6041_CLOSED_FAILURE_CHECKPOINT=" + checkpoint, flush=True)
            if checkpoint in {"button_not_clicked", "widget_geometry", "x11_hit_mismatch"}:
                # Closed, fixture-independent input-delivery diagnostics.
                print("P1_6041_GTK_HIT=" + (
                    "yes" if state.get("geometry_hit") else "no"
                ), flush=True)
                print("P1_6041_TARGET_ONSCREEN=" + (
                    "yes" if state.get("geometry_onscreen") else "no"
                ), flush=True)
                print("P1_6041_X11_TARGET_WINDOW=" + (
                    "yes" if state.get("x11_target_window") else "no"
                ), flush=True)
                print("P1_6041_X11_WIDGET_HIT=" + (
                    "yes" if state.get("x11_widget_hit") else "no"
                ), flush=True)
                print("P1_6041_XTEST_BUTTON_QUEUED=" + (
                    "yes" if state.get("xtest_button_queued") else "no"
                ), flush=True)
                print("P1_6041_NATIVE_POINTER=" + (
                    "seen" if state["native_pointer_seen"] else "absent"
                ), flush=True)
                print("P1_6041_NATIVE_KEY=" + (
                    "seen" if state["native_key_seen"] else "absent"
                ), flush=True)
                pointer = "seen" if state["pointer_seen"] else "absent"
                keyboard = "seen" if state["key_seen"] else "absent"
                print("P1_6041_INPUT_POINTER=" + pointer, flush=True)
                print("P1_6041_INPUT_KEY=" + keyboard, flush=True)
                print("P1_6041_BUTTON_SENSITIVE=" + (
                    "yes" if button.get_sensitive() else "no"
                ), flush=True)
        finally:
            discovery.discover_linux_usb_volumes = original_discovery
            window._closed = True
            window.destroy()
            device.close()
            save()
        app.quit()
        return False

    def on_activate(application):
        window.set_application(application)
        window.present()
        checks = run_checks()

        def advance():
            # Every yield relinquishes GTK's main context. GDK dispatches
            # native XTEST button/key events before the next continuation,
            # without ever invoking signals or the product handler directly.
            try:
                interval = next(checks)
            except StopIteration:
                return False
            GLib.timeout_add(interval, advance)
            return False

        GLib.idle_add(advance)

    app.connect("activate", on_activate)
    app.run([])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
