#!/usr/bin/env python3
"""P1 #4985: real MainWindow GTK reader target reservation and detail repaint.

Runs with an isolated SQLite/config/HOME and one local synthetic artifact.
No USB mount, network, supplier or automatic transfer. Native Gtk.ListView
selection and DetailPanel must update on the *same book key* after the
explicit reserve button is clicked; GTK synthetic data is NOT hardware proof.
Private PNGs stay on the runner, never in public CI artifact export.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import ImageGrab

from cifereca.core import CiferecaCore
from cifereca.core.library_audit_roots import configured_identity_roots
from cifereca.gui.main_window import MainWindow


ROOT = Path(__file__).resolve().parents[1]
STAGES = frozenset({
    "preflight", "core", "fixture", "gtk", "reader", "selection",
    "reservation", "reservation_commit", "repaint", "intent_cancel",
    "intent_confirm", "intent_repaint", "profile_b", "profile_a", "finish",
})


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1366, 1600), required=True)
    args = parser.parse_args()
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("GTK4/Xvfb X11 requis")
    destination = args.out.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    phase = ["preflight"]
    def closed_excepthook(_kind, _error, _traceback):
        print("P1_4985_GTK_CLOSED_STAGE=" + phase[0], flush=True)
    sys.excepthook = closed_excepthook
    scratch = TemporaryDirectory(prefix="cifereca-4985-target-")
    work = Path(scratch.name)
    home = work / "home"
    home.mkdir()
    os.environ["HOME"] = str(home)
    for key, folder in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_STATE_HOME", "state"),
    ):
        os.environ[key] = str(work / folder)
    os.environ["CIFERECA_PLUGIN_DEPS_OFFLINE"] = "1"
    (work / "config.json").write_text(json.dumps({
        "identities": [
            {"id": "alice", "label": "Alice (fixture)", "directory_name": "alice"},
            {"id": "bob", "label": "Bob (fixture)", "directory_name": "bob"},
        ],
    }) + "\n", encoding="utf-8")
    core = None
    phase[0] = "core"
    core = CiferecaCore.open(
        config_path=work / "config.json",
        database_path=work / "state.sqlite3",
        no_plugins=True, activate_plugins=False,
    )
    assert core.library.current_identity is not None
    assert core.library.current_identity.identity_id == "alice"
    phase[0] = "fixture"
    root = configured_identity_roots(core.config, "alice")["alice"][0]
    root.mkdir(parents=True, exist_ok=True)
    payload = b"Local only: USB target reservation GTK fixture"
    digest = sha256(payload).hexdigest()
    artifact_id = "sha256:" + digest
    local = root / "fixture.epub"
    local.write_bytes(payload)
    core.storage.artifact_put({
        "artifact_id": artifact_id, "sha256": digest, "size": len(payload),
        "media_format": "epub", "metadata": {"artifact_kind": "file"},
    })
    placement = core.storage.placement_put({
        "artifact_id": artifact_id, "path": str(local),
        "identity_id": "alice", "provider_id": "local",
        "source_item_id": "reader-book", "state": "present", "metadata": {},
    })
    for identity, device in (("alice", "reader-a"), ("bob", "reader-b")):
        core.storage.device_usb_binding_declare(
            identity_id=identity, device_id=device,
            label="Liseuse fictive",
            usb_fingerprint="uuid-" + identity + "-0001", enabled=True,
        )
    core.storage.device_usb_intent_set(
        identity_id="alice", device_id="reader-a",
        artifact_id=artifact_id, desired_state="keep",
    )
    entry = {
        "key": "reader-book", "identity_id": "alice",
        "artifact_id": artifact_id, "placement_id": placement,
        "title": "Livre fictif", "author": "Autrice fictive",
        "local": True, "provider_id": "local", "thumbnail_url": None,
    }
    before = core.storage.device_placement_get("reader-a", artifact_id)
    assert before["target_relative_path"] is None
    assert before["observed_state"] == "unknown"
    state = {"window": None, "app": None, "start": monotonic(), "deadline": monotonic() + 55}
    result_code = [1]
    report = {
        "status": "RUNNING", "width": args.width, "backend": "GTK4/X11",
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "checks": {}, "usb_reads": 0, "usb_writes": 0, "provider_calls": 0,
    }

    def save():
        (destination / "screen.json").write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def fail(_error):
        if report["status"] != "RUNNING":
            return
        report["status"] = "FAIL"
        report["closed_failure_phase"] = phase[0]
        print("P1_4985_GTK_CLOSED_STAGE=" + phase[0], flush=True)
        save()
        state["app"].quit()

    def later(delay_ms, action):
        def callback():
            if report["status"] != "RUNNING":
                return False
            try:
                if monotonic() > state["deadline"]:
                    raise TimeoutError("target GTK bounded timeout")
                action()
            except Exception as exc:
                fail(exc)
            return False
        GLib.timeout_add(delay_ms, callback)

    def actions():
        return [
            widget for widget in walk(state["window"].readers_page.detail)
            if isinstance(widget, Gtk.Button)
            and getattr(widget, "_cifereca_action_id", None)
                == "reader-usb-target-reserve"
        ]

    def intent_actions(operation):
        return [
            widget for widget in walk(state["window"].readers_page.detail)
            if isinstance(widget, Gtk.Button)
            and getattr(widget, "_cifereca_action_id", None)
                == "reader-usb-intent:" + operation
        ]

    def has_target():
        return any(
            isinstance(widget, Adw.ActionRow)
            and widget.get_title() == "Chemin réservé"
            for widget in walk(state["window"].readers_page.detail)
        )

    def begin():
        phase[0] = "reader"
        window = state["window"]
        window._library_entries_cache = [entry]
        window.navigate("readers")
        assert window.view_stack.get_visible_child_name() == "readers"
        later(350, select)

    def select():
        phase[0] = "selection"
        window = state["window"]
        page = window.readers_page
        assert page.store.get_n_items() == 1
        item = page.store.get_item(0)
        assert item.key == "reader-book"
        assert page.select_key("reader-book")
        assert page.selection.get_selected_item().key == "reader-book"
        assert not has_target()
        assert len(actions()) == 1
        assert len(intent_actions("remove")) == 1
        report["checks"]["real_mainwindow_selected_reader_book"] = True
        report["checks"]["real_gtk_reader_remove_wish_visible"] = True
        later(150, reserve)

    # GTK may finish allocating the real DetailPanel a few frames after
    # selection, especially at 1366. A user cannot click an unmapped button:
    # wait for that exact UI affordance instead of racing a fixed 150 ms.
    reserve_deadline = [None]

    def reserve():
        phase[0] = "reservation"
        if reserve_deadline[0] is None:
            reserve_deadline[0] = monotonic() + 5.0
        found = actions()
        if len(found) == 1 and not found[0].get_mapped():
            # On narrower windows, AdaptiveActionBar moves secondary actions
            # into the real GTK "Plus d’actions" popover. A hidden popover
            # button is not a user-clickable action until that menu is opened.
            bar = state["window"].readers_page.detail.action_scroll
            if (found[0].get_parent() is getattr(bar, "overflow_box", None)
                    and bar.overflow_button.get_mapped()
                    and not bar.overflow_popover.get_mapped()):
                bar.overflow_popover.popup()
        if len(found) != 1 or not found[0].get_mapped():
            if monotonic() < reserve_deadline[0]:
                later(120, reserve)
                return
            raise AssertionError("Reserve action never mapped")
        button = found[0]
        phase[0] = "reservation_commit"
        button.emit("clicked")
        after = core.storage.device_placement_get("reader-a", artifact_id)
        assert after["target_relative_path"] == "Books/Cifereca/" + digest + ".epub"
        assert after["observed_state"] == "unknown"
        assert after["last_observed_at"] is None
        assert local.read_bytes() == payload
        report["checks"]["exact_explicit_sqlite_target_no_usb_io"] = True
        later(300, repaint)

    def repaint():
        phase[0] = "repaint"
        window = state["window"]
        page = window.readers_page
        assert page.selection.get_selected_item() is not None
        assert page.selection.get_selected_item().key == "reader-book"
        assert page.store.get_n_items() == 1
        assert has_target(), "Detail has not refreshed after reservation"
        assert not actions(), "Stale reserve action survived a successful write"
        assert len(intent_actions("remove")) == 1
        assert "Destination USB réservée" in window._reader_status_label.get_text()
        report["checks"]["real_gtk_target_detail_repaint_same_key"] = True
        picture = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert picture.size == (args.width, 1000 if args.width == 1600 else 900)
        assert picture.getbbox() is not None
        target = destination / "screen.png"
        picture.save(target)
        report["private_screen_sha256"] = sha256(target.read_bytes()).hexdigest()
        later(80, cancel_remove)

    intent_deadline = [None]

    def mapped_intent_button():
        found = intent_actions("remove")
        if len(found) != 1:
            return None
        button = found[0]
        if not button.get_mapped():
            bar = state["window"].readers_page.detail.action_scroll
            if (button.get_parent() is getattr(bar, "overflow_box", None)
                    and bar.overflow_button.get_mapped()
                    and not bar.overflow_popover.get_mapped()):
                bar.overflow_popover.popup()
        return button if button.get_mapped() else None

    def cancel_remove():
        phase[0] = "intent_cancel"
        if intent_deadline[0] is None:
            intent_deadline[0] = monotonic() + 5.0
        button = mapped_intent_button()
        if button is None:
            if monotonic() < intent_deadline[0]:
                later(120, cancel_remove)
                return
            raise AssertionError("Remove wish action not mapped")
        before = core.storage.device_placement_get("reader-a", artifact_id)
        button.emit("clicked")
        dialog = getattr(state["window"], "_reader_intent_dialog", None)
        assert isinstance(dialog, Adw.MessageDialog)
        assert core.storage.device_placement_get("reader-a", artifact_id) == before
        dialog.emit("response", "cancel")
        assert core.storage.device_placement_get("reader-a", artifact_id) == before
        report["checks"]["real_gtk_remove_requires_independent_confirmation"] = True
        later(140, confirm_remove)

    def confirm_remove():
        phase[0] = "intent_confirm"
        button = mapped_intent_button()
        if button is None:
            later(120, confirm_remove)
            return
        before = core.storage.device_placement_get("reader-a", artifact_id)
        button.emit("clicked")
        dialog = getattr(state["window"], "_reader_intent_dialog", None)
        assert isinstance(dialog, Adw.MessageDialog)
        assert core.storage.device_placement_get("reader-a", artifact_id) == before
        dialog.emit("response", "remove")
        after = core.storage.device_placement_get("reader-a", artifact_id)
        assert after["desired_state"] == "remove"
        assert after["target_relative_path"] == before["target_relative_path"]
        assert after["observed_state"] == "unknown"
        assert after["last_observed_at"] is None
        assert local.read_bytes() == payload
        report["checks"]["real_gtk_confirmed_remove_only_updates_sqlite_wish"] = True
        later(180, repaint_remove)

    def repaint_remove():
        phase[0] = "intent_repaint"
        window = state["window"]
        assert window.readers_page.selection.get_selected_item() is not None
        assert window.readers_page.selection.get_selected_item().key == "reader-book"
        assert len(intent_actions("keep")) == 1
        assert not intent_actions("remove")
        assert not actions()
        assert has_target()
        assert "Aucun fichier transféré ou supprimé" in window._reader_status_label.get_text()
        report["checks"]["real_gtk_remove_wish_detail_repaint_same_key"] = True
        later(80, switch_b)

    def switch_b():
        phase[0] = "profile_b"
        window = state["window"]
        window.service.select_identity("bob")
        window._identity_context_generation += 1
        window._account_context_generation += 1
        window._render_readers()
        assert window._reader_scope_identity == "bob"
        assert window._reader_selected_device_id == "reader-b"
        assert window.readers_page.store.get_n_items() == 0
        report["checks"]["real_gtk_bob_does_not_see_alice"] = True
        later(100, switch_a)

    def switch_a():
        phase[0] = "profile_a"
        window = state["window"]
        window.service.select_identity("alice")
        window._identity_context_generation += 1
        window._account_context_generation += 1
        window._render_readers()
        assert window._reader_scope_identity == "alice"
        assert window._reader_selected_device_id == "reader-a"
        assert window.readers_page.store.get_n_items() == 1
        assert window.readers_page.select_key("reader-book")
        assert has_target() and not actions()
        assert core.storage.device_placement_get(
            "reader-a", artifact_id,
        )["observed_state"] == "unknown"
        report["checks"]["real_gtk_A_B_A_target_persisted_not_presence"] = True
        phase[0] = "finish"
        report["status"] = "PASS"
        save()
        result_code[0] = 0
        state["app"].quit()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.ReaderTargetP14985")

        def do_activate(self):
            try:
                phase[0] = "gtk"
                w = MainWindow(self, core.config, core.library, core.diagnostics)
                state["window"] = w
                w.set_default_size(args.width - 60, 900 if args.width == 1600 else 790)
                w.present()
                later(480, begin)
            except Exception as exc:
                fail(exc)

        def do_shutdown(self):
            try:
                core.close()
            finally:
                try:
                    Gio.Application.do_shutdown(self)
                finally:
                    scratch.cleanup()

    app = Application()
    state["app"] = app
    GLib.timeout_add_seconds(
        58, lambda: (fail(TimeoutError("watchdog")), False)[1]
        if report["status"] == "RUNNING" else False,
    )
    app.run([sys.argv[0]])
    if not (destination / "screen.json").exists():
        report["status"] = "FAIL"
        report["closed_failure_phase"] = phase[0]
        save()
    return result_code[0]


if __name__ == "__main__":
    raise SystemExit(main())
