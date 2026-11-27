#!/usr/bin/env python3
"""P1 #4985: real MainWindow GTK reader target reservation and detail repaint.

Runs with an isolated SQLite/config/HOME and one local synthetic artifact.
No USB hardware, network, supplier or automatic transfer. Native Gtk.ListView
selection and DetailPanel must update on the *same book key* after the
explicit reserve button is clicked; GTK synthetic data is NOT hardware proof.
The grouped receipt adds a real isolated pseudo-reader filesystem and Core transport.
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
    "intent_confirm", "intent_repaint", "execute_prepare",
    "execute_cancel", "execute_confirm", "execute_result",
    "profile_b", "profile_a", "native_fixture", "native_configuration",
    "native_send", "native_conflict", "native_confirmation", "native_cancellation",
    "native_copy", "native_copy_admission", "native_copy_progress",
    "native_copy_navigation", "native_copy_result", "native_copy_observation",
    "native_idempotence", "native_removal",
    "native_usb_a", "native_usb_b", "native_usb_isolation",
    "native_usb_b_source", "native_usb_b_profile", "native_usb_b_naming_dialog", "native_usb_b_naming_commit", "native_usb_b_menu", "native_usb_b_wish", "native_usb_b_reservation",
    "native_usb_discovery",
    "native_usb_preview", "native_usb_admission", "native_usb_copy_result", "native_usb_observation", "finish",
})


# Closed verdicts only. No path, title, raw exception or plugin detail may
# become a public diagnostic. The runner independently checks this vocabulary.
COPY_VERDICTS = frozenset({
    "copy_not_reached",
    "copy_worker_timeout",
    "copy_result_unclassified",
    "copy_completed_physical_mismatch",
    "copy_delivery_timeout",
    "copy_result_count_mismatch",
    "copy_sync_receipt_mismatch",
    "usb_scan_unverified",
    "usb_scope_changed",
    "usb_observation_unavailable",
    "usb_complete_scan_observed_only",
    "observation_not_reached",
    "observation_result_unclassified",
    "usb_copy_source_unverified",
    "usb_copy_directory_unavailable",
    "usb_copy_request_invalid",
    "usb_copy_authorization_unverified",
    "usb_stage_transport_unavailable",
    "usb_copy_consent_already_used",
    "usb_copy_target_review_refused",
    "usb_copy_target_not_clear",
    "usb_copy_journal_conflict",
    "usb_stage_request_invalid",
    "usb_stage_unsupported",
    "usb_stage_source_mismatch",
    "usb_stage_directory_unverified",
    "usb_stage_pending_not_current",
    "usb_stage_already_exists",
    "usb_stage_incomplete",
    "usb_stage_scope_changed",
    "usb_stage_file_unverified",
    "usb_stage_io_failed",
    "usb_stage_transport_failed",
    "usb_recovery_pin_unverified",
    "usb_recovery_checkpoint_refused",
    "usb_recovery_checkpoint_failed",
    "usb_publish_request_invalid",
    "usb_publish_unsupported",
    "usb_publish_stage_unverified",
    "usb_publish_pending_not_current",
    "usb_publish_directory_unbounded",
    "usb_publish_target_occupied",
    "usb_publish_stage_changed",
    "usb_publish_postcheck_failed",
    "usb_publish_io_failed",
    "usb_publish_revalidation_failed",
    "usb_copy_receipt_uncommitted",
    "usb_copy_final_changed",
    "usb_copy_reader_busy",
    "usb_copy_operation_failed",
    "sync_blocked",
    "sync_unavailable",
    "sync_partial",
    "sync_pending",
    "sync_cancelled",
    "sync_completed",
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
    from io import BytesIO
    from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, value in (
            ("mimetype", "application/epub+zip"),
            ("META-INF/container.xml",
             '<container><rootfiles><rootfile full-path="OEBPS/package.opf"/></rootfiles></container>'),
            ("OEBPS/package.opf", "<package/>"),
        ):
            archive.writestr(ZipInfo(name), value, compress_type=ZIP_DEFLATED)
    payload = stream.getvalue()
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
        "checks": {}, "legacy_synthetic_usb_reads": 0,
        "legacy_synthetic_usb_writes": 0, "provider_calls": 0,
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
        later(80, install_synthetic_executor)

    fake_execution = []
    fake_start = [None]
    fake_volume = [None]
    fake_epoch = [None]
    production = {}

    def install_synthetic_executor():
        # This is a synthetic GTK/UI integration test ONLY. The fake broker
        # never touches the volume; production Core independently attests a
        # real USB block mount and its managed-copy receipt under a lock.
        phase[0] = "execute_prepare"
        from cifereca.core.device_usb_discovery import MountedUsbVolume
        from cifereca.core.device_usb_dry_run import UsbDryRun
        from cifereca.core.device_usb_preview import UsbPreviewRow
        from cifereca.core.device_usb_sync_engine import UsbSyncResult
        from cifereca.gui import reader_usb_preview_actions as preview_actions
        fake_volume[0] = MountedUsbVolume(
            "uuid-alice-0001", work / "synthetic-unmounted-volume",
            "vfat", "Synthetic (not hardware)", 100_000_000, 8, 5, 71,
        )
        production["discover"] = preview_actions._discover_mounted_binding
        preview_actions._discover_mounted_binding = lambda **_: fake_volume[0]
        class FakeOnlyUsbBroker:
            def completed_copy_operation(self, **kwargs):
                assert kwargs == {
                    "identity_id": "alice", "device_id": "reader-a",
                    "artifact_id": artifact_id,
                }
                return "a" * 32

            def preview(self, **kwargs):
                assert kwargs["identity_id"] == "alice"
                assert kwargs["device_id"] == "reader-a"
                assert kwargs["mountpoint"] == fake_volume[0].mountpoint
                return UsbDryRun(
                    "alice", "reader-a", "scanned", True, 1,
                    (UsbPreviewRow(
                        artifact_id, "remove", "present", "unknown", None,
                        "remove_candidate",
                        "managed_removal_requires_confirmation",
                    ),),
                )
            def admit_removal(
                self, *, confirmation, mountpoint, expected_profile_epoch,
                mount_key,
            ):
                assert mount_key == fake_volume[0].selection_key()
                assert expected_profile_epoch == fake_epoch[0]
                assert confirmation.artifact_id == artifact_id
                assert confirmation.copy_operation_id == "a" * 32
                assert confirmation.target_relative_path == (
                    "Books/Cifereca/" + digest + ".epub"
                )
                assert mountpoint == fake_volume[0].mountpoint
                def work():
                    fake_execution.append(confirmation)
                    return UsbSyncResult("completed", removed=1, observed=True)
                return work
        window = state["window"]
        manager = window.plugin_manager
        assert manager is not None
        fake_epoch[0] = window.service._account_context_epoch
        production["broker"] = manager.broker("reader.usb")
        manager._brokers["reader.usb"] = FakeOnlyUsbBroker()
        # Refresh only the selected detail: the list-row version is
        # intentionally unchanged after synthetic test-broker injection.
        from cifereca.gui.reader_page_controller import reader_selected
        selected_row = window.readers_page.selection.get_selected_item()
        assert selected_row is not None and selected_row.key == "reader-book"
        reader_selected(window, selected_row)
        assert window.readers_page.selection.get_selected_item().key == "reader-book"
        fake_start[0] = monotonic() + 7
        later(150, click_synthetic_execution)

    def mapped_execution_button():
        found = [
            widget for widget in walk(state["window"].readers_page.detail)
            if isinstance(widget, Gtk.Button)
            and getattr(widget, "_cifereca_action_id", None)
                == "reader-usb-execute-remove"
        ]
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

    def click_synthetic_execution():
        phase[0] = "execute_prepare"
        button = mapped_execution_button()
        if button is None:
            if monotonic() < fake_start[0]:
                later(120, click_synthetic_execution)
                return
            raise AssertionError("Synthetic execute action not mapped")
        button.emit("clicked")
        assert not fake_execution
        assert local.read_bytes() == payload
        later(150, cancel_synthetic_execution)

    def cancel_synthetic_execution():
        phase[0] = "execute_cancel"
        dialog = getattr(state["window"], "_reader_usb_execute_dialog", None)
        if dialog is None:
            if monotonic() < fake_start[0]:
                later(120, cancel_synthetic_execution)
                return
            raise AssertionError("Synthetic execution dialog unavailable")
        assert isinstance(dialog, Adw.MessageDialog)
        assert not fake_execution
        dialog.emit("response", "cancel")
        report["checks"]["real_gtk_second_destructive_confirmation_cancelled"] = True
        later(160, start_synthetic_confirmation)

    def start_synthetic_confirmation():
        phase[0] = "execute_confirm"
        button = mapped_execution_button()
        if button is None:
            if monotonic() < fake_start[0]:
                later(120, start_synthetic_confirmation)
                return
            raise AssertionError("Synthetic execution action not mapped again")
        button.emit("clicked")
        later(120, confirm_synthetic_execution)

    def confirm_synthetic_execution():
        phase[0] = "execute_confirm"
        dialog = getattr(state["window"], "_reader_usb_execute_dialog", None)
        if dialog is None:
            if monotonic() < fake_start[0]:
                later(120, confirm_synthetic_execution)
                return
            raise AssertionError("Second confirmation not displayed")
        assert not fake_execution
        dialog.emit("response", "delete")
        later(150, synthetic_execution_result)

    def synthetic_execution_result():
        phase[0] = "execute_result"
        if len(fake_execution) != 1:
            if monotonic() < fake_start[0]:
                later(120, synthetic_execution_result)
                return
            raise AssertionError("Synthetic single-book broker not called once")
        assert local.read_bytes() == payload
        assert core.storage.device_placement_get(
            "reader-a", artifact_id,
        )["observed_state"] == "unknown"
        assert "Retrait USB supervisé terminé" in (
            state["window"]._reader_status_label.get_text()
        )
        report["checks"]["real_gtk_core_broker_only_after_distinct_confirmation"] = True
        report["checks"]["synthetic_broker_never_claims_usb_hardware"] = True
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
        native_grouped_receipt()
        phase[0] = "finish"
        report["status"] = "PASS"
        save()
        result_code[0] = 0
        state["app"].quit()

    def native_grouped_receipt():
        """Real GTK widgets, TaskRunner, Core broker, SQLite and local FS.

        Start with the two USB bindings offline, then detect two ad hoc
        filesystem roots. Only USB discovery/attestation
        is simulated for those volumes. The pseudo-reader keeps native Linux
        attestation. Resolve the production storage.target in the isolated
        no-plugin registry; broker, scanner, executor and queue stay real.
        """
        from contextlib import ExitStack
        from dataclasses import replace
        from importlib import import_module
        from threading import Event
        from time import sleep
        from types import SimpleNamespace
        from unittest.mock import patch
        import runpy
        from cifereca.core.device_usb_execution_broker import ReaderUsbExecutionBrokerImpl
        from cifereca.gui import reader_usb_preview_actions as preview_actions
        from cifereca.plugin_api import USB_STAGE_CAPABILITY

        phase[0] = "native_fixture"
        window = state["window"]
        db = core.storage
        manager = window.plugin_manager
        facade = production["broker"]
        assert type(facade) is ReaderUsbExecutionBrokerImpl
        manager._brokers["reader.usb"] = facade
        preview_actions._discover_mounted_binding = production["discover"]
        # The earlier synthetic single-book exercise may have loaded a GTK
        # action module while its discovery shim was installed. Restore the
        # production function in every native entry point, including aliases.
        from cifereca.gui import reader_local_actions, reader_sync_actions
        reader_local_actions._discover_mounted_binding = production["discover"]
        reader_sync_actions._discover_mounted_binding = production["discover"]
        transport = runpy.run_path(str(ROOT / "src/plugins/storage-usb/cifereca_storage_usb.py"))[
            "UsbMassStorageTarget"]()
        original_services = manager.services
        resolved = SimpleNamespace(
            plugin_id="storage.usb", registry_generation=manager.registry_generation,
            capabilities=(USB_STAGE_CAPABILITY,), service=transport,
        )
        manager.services = lambda category: (
            (resolved,) if category == "storage.target" else original_services(category))
        target_root = work / "native-local-reader"
        target_root.mkdir()
        device = "reader-local-a"
        db.device_local_reader_declare(identity_id="alice", device_id=device,
            label="Liseuse locale fixture", root=target_root, enabled=True)
        binding = db.device_binding_get(device)
        fingerprint = binding["metadata"]["usb_fingerprint"]
        db.device_usb_reader_formats_set(identity_id="alice", device_id=device,
            usb_fingerprint=fingerprint, supported_formats=("epub",))
        assert {row["device_id"] for row in db.device_bindings()} == {
            "reader-a", "reader-b", device}
        context = GLib.MainContext.default()
        deadline = min(state["deadline"], monotonic() + 42)

        def wait(predicate, *, seconds=6):
            limit = min(deadline, monotonic() + seconds)
            while monotonic() < limit and report["status"] == "RUNNING":
                context.iteration(False)
                value = predicate()
                if value:
                    return value
                sleep(.01)
            raise AssertionError("Native GTK checkpoint timeout")

        def header(action):
            return wait(lambda: next((widget for widget in walk(window.readers_page.header_actions)
                if isinstance(widget, Gtk.Button) and widget.get_mapped()
                and getattr(widget, "_cifereca_action_id", None) == action), None))

        def select_reader(device_id):
            window._reader_device_dropdown.set_selected(
                window._reader_device_ids.index(device_id))
            assert window._reader_selected_device_id == device_id

        def dialog_with_heading(heading):
            def find():
                windows = Gtk.Window.get_toplevels()
                for index in range(windows.get_n_items()):
                    candidate = windows.get_item(index)
                    if (isinstance(candidate, Adw.MessageDialog)
                            and candidate.get_mapped() and candidate.get_heading() == heading):
                        return candidate
                return None
            return wait(find)

        def sync_dialog():
            sync = header("reader-sync-delta")
            wait(sync.get_sensitive)
            sync.emit("clicked")
            def mapped_dialog():
                dialog = getattr(window, "_reader_sync_dialog", None)
                return dialog if dialog is not None and dialog.get_mapped() else None
            return wait(mapped_dialog)

        def intent():
            return db.device_placement_get(device, artifact_id)

        try:
            phase[0] = "native_configuration"
            window._render_readers()
            select_reader(device)
            assert set(window._reader_device_ids) == {"reader-a", device}
            header("reader-directory-config").emit("clicked")
            directory_dialog = dialog_with_heading("Dossier géré de la liseuse")
            directory_dialog.get_extra_child().get_first_child().set_text("Reading/Library")
            directory_dialog.emit("response", "save")
            assert db.device_binding_get(device)["target_directory"] == "Reading/Library"
            header("reader-setup-directory").emit("clicked")
            setup_dialog = dialog_with_heading("Initialiser le dossier géré ?")
            setup_dialog.emit("response", "setup")
            managed = target_root / "Reading" / "Library"
            wait(lambda: managed.is_dir() and "initialisé" in window._reader_status_label.get_text())
            third = managed / "third-party.txt"
            third.write_bytes(b"untouched")
            assert intent() is None

            phase[0] = "native_send"
            def menu_choices(item=entry):
                window.navigate("library")
                window._render_library([item])
                assert window.library_page.select_key(item["key"])
                # Force the actual selection controller to rebuild its action
                # lease even when the library's row key has not changed.
                window._library_selected(window.library_page.selection.get_selected_item())
                detail = window.library_page.detail
                menu = wait(lambda: next((widget for widget in walk(detail)
                    if isinstance(widget, Gtk.MenuButton)
                    and getattr(widget, "_cifereca_action_id", None) == "usb-intents"), None))
                if not menu.get_mapped():
                    bar = detail.action_scroll
                    assert menu.get_parent() is bar.overflow_box
                    bar.overflow_popover.popup()
                wait(menu.get_mapped)
                menu.get_popover().popup()
                box = menu._cifereca_send_choices
                choices = {getattr(widget, "_cifereca_action_id", None): widget
                           for widget in walk(box) if isinstance(widget, Gtk.Button)}
                return menu, choices

            menu, choices = menu_choices()
            offline = choices["usb-intent:keep:reader-a"]
            stale = choices["usb-intent:keep:" + device]
            wait(offline.get_mapped)
            offline.emit("clicked")
            offline_intent = db.device_placement_get("reader-a", artifact_id)
            assert offline_intent["desired_state"] == "keep"
            assert offline_intent["observed_state"] == "unknown"
            assert db.device_placements(device_id="reader-b") == []
            menu.get_popover().popdown()
            window.navigate("readers")
            stale.emit("clicked")
            assert intent() is None
            menu, choices = menu_choices()
            local_choice = choices["usb-intent:keep:" + device]
            wait(local_choice.get_mapped)
            local_choice.emit("clicked")
            assert intent()["desired_state"] == "keep"
            assert intent()["observed_state"] == "unknown"
            assert intent()["target_relative_path"] == "Reading/Library/" + digest + ".epub"
            final = target_root / intent()["target_relative_path"]
            assert not final.exists()
            menu.get_popover().popdown()
            window.navigate("readers")
            window._library_entries_cache = [entry]
            window._render_readers()
            select_reader(device)
            report["checks"]["native_send_offline_and_local_three_destinations"] = True

            phase[0] = "native_conflict"
            final.write_bytes(b"external conflict")
            sync = header("reader-sync-delta")
            sync.emit("clicked")
            wait(lambda: sync.get_sensitive() and "bloqué(s)" in window._reader_status_label.get_text())
            assert getattr(window, "_reader_sync_dialog", None) is None
            assert "1 bloqué(s)" in window._reader_status_label.get_text()
            assert final.read_bytes() == b"external conflict"
            assert intent()["observed_state"] == "unknown"
            final.unlink()  # fixture-owned external conflict, not application I/O
            report["checks"]["native_conflict_no_overwrite"] = True

            phase[0] = "native_confirmation"
            dialog = sync_dialog()
            assert isinstance(dialog, Adw.MessageDialog) and not final.exists()
            assert "1 envoi(s)" in dialog.get_body()
            dialog.emit("response", "cancel")
            assert not final.exists() and not getattr(window, "_reader_sync_active", False)
            dialog = sync_dialog()
            select_reader("reader-a")
            select_reader(device)
            dialog.emit("response", "sync")
            assert not final.exists() and not getattr(window, "_reader_sync_active", False)
            report["checks"]["native_global_confirmation_cancel_and_reader_aba"] = True

            # Deterministic interruption/navigation checkpoint at the real
            # Core progress boundary; no fabricated result or task queue.
            original_admit = type(facade).admit_delta
            from cifereca.core import device_usb_sync_engine as sync_engine
            from cifereca.core.device_usb_copy_supervisor import UsbCopyExecution
            from cifereca.core.device_usb_sync_engine import UsbSyncResult
            original_copy = sync_engine.execute_usb_library_copy
            original_observe = sync_engine.record_verified_usb_observation
            from cifereca.core.device_usb_observation_record import UsbRecordedObservation

            def run_held(*, cancel):
                entered, release = Event(), Event()
                copy_verdicts, sync_verdicts, observations = [], [], []

                def observed_copy(**kwargs):
                    outcome = original_copy(**kwargs)
                    if type(outcome) is UsbCopyExecution:
                        copy_verdicts.append(outcome)
                    return outcome

                def observed_scan(**kwargs):
                    outcome = original_observe(**kwargs)
                    if type(outcome) is UsbRecordedObservation:
                        observations.append(outcome)
                    return outcome

                def closed_verdict():
                    if copy_verdicts and copy_verdicts[-1].status != "completed":
                        reason = copy_verdicts[-1].reason
                        return reason if reason in COPY_VERDICTS else "copy_result_unclassified"
                    if sync_verdicts:
                        result = sync_verdicts[-1]
                        if result.status != "completed":
                            if observations and observations[-1].status != "recorded":
                                reason = observations[-1].reason
                                return reason if reason in COPY_VERDICTS else "observation_result_unclassified"
                            if copy_verdicts and not observations:
                                return "observation_not_reached"
                            candidate = "sync_" + result.status
                            return candidate if candidate in COPY_VERDICTS else "copy_result_unclassified"
                        if result != UsbSyncResult("completed", copied=1, observed=True):
                            return "copy_sync_receipt_mismatch"
                        if len(copy_verdicts) != 1:
                            return "copy_result_count_mismatch"
                        if not final.is_file() or final.read_bytes() != payload:
                            return "copy_completed_physical_mismatch"
                        return "sync_completed"
                    return "copy_not_reached"
                def admit(broker, **kwargs):
                    callback = kwargs["progress"]
                    def progress(*args):
                        callback(*args)
                        if args[0] == 0:
                            entered.set()
                            release.wait(12)
                    operation = original_admit(broker, **(kwargs | {"progress": progress}))
                    def execute():
                        result = operation()
                        if type(result) is UsbSyncResult:
                            sync_verdicts.append(result)
                        return result
                    return execute
                with (patch.object(type(facade), "admit_delta", admit),
                      patch.object(sync_engine, "execute_usb_library_copy", observed_copy),
                      patch.object(sync_engine, "record_verified_usb_observation", observed_scan)):
                    try:
                        if not cancel:
                            phase[0] = "native_copy_admission"
                        dialog = sync_dialog()
                        dialog.emit("response", "sync")
                        if not cancel:
                            phase[0] = "native_copy_progress"
                        wait(entered.is_set)
                        assert window._reader_sync_active
                        if cancel:
                            header("reader-sync-cancel").emit("clicked")
                        else:
                            phase[0] = "native_copy_navigation"
                            window.navigate("library")
                            wait(lambda: "0/1 fichiers" in window.busy_label.get_text()
                                 and window.busy_revealer.get_reveal_child())
                            assert window.view_stack.get_visible_child_name() == "library"
                    finally:
                        release.set()
                    if not cancel:
                        phase[0] = "native_copy_result"
                    try:
                        wait(lambda: not getattr(window, "_reader_sync_active", False), seconds=12)
                    except AssertionError:
                        if not cancel:
                            report["closed_copy_result"] = ("copy_delivery_timeout" if sync_verdicts
                                                            else "copy_worker_timeout")
                        raise
                if cancel:
                    assert len(sync_verdicts) == 1 and sync_verdicts[0].status == "cancelled"
                    assert not copy_verdicts
                else:
                    report["closed_copy_result"] = closed_verdict()
                    assert len(sync_verdicts) == 1 and sync_verdicts[0] == UsbSyncResult(
                        "completed", copied=1, observed=True)
                    assert len(copy_verdicts) == 1 and copy_verdicts[0].status == "completed"
                    assert len(observations) == 1 and observations[0].status == "recorded"

            phase[0] = "native_cancellation"
            run_held(cancel=True)
            assert not final.exists()
            assert "interrompue" in window._reader_status_label.get_text()
            report["checks"]["native_cooperative_cancel_before_mutation"] = True

            phase[0] = "native_copy"
            run_held(cancel=False)
            if not (final.is_file() and final.read_bytes() == local.read_bytes() == payload):
                report["closed_copy_result"] = "copy_completed_physical_mismatch"
                raise AssertionError("Native copy bytes differ")
            phase[0] = "native_copy_observation"
            assert intent()["observed_state"] == "present"
            assert intent()["last_observed_at"] is not None
            assert db.device_placement_get("reader-a", artifact_id) == offline_intent
            window.navigate("readers")
            window._library_entries_cache = [entry]
            window._render_readers()
            select_reader(device)
            report.pop("closed_copy_result", None)
            report["checks"]["native_real_copy_observe_progress_across_navigation"] = True

            phase[0] = "native_idempotence"
            stat_before = final.stat()
            sync = header("reader-sync-delta")
            wait(sync.get_sensitive)
            sync.emit("clicked")
            wait(lambda: sync.get_sensitive() and "1 identique(s)" in window._reader_status_label.get_text())
            assert getattr(window, "_reader_sync_dialog", None) is None
            assert (final.stat().st_ino, final.stat().st_mtime_ns) == (
                stat_before.st_ino, stat_before.st_mtime_ns)
            report["checks"]["native_idempotent_no_recopy"] = True

            phase[0] = "native_removal"
            assert window.readers_page.select_key("reader-book")
            remove = wait(mapped_intent_button)
            remove.emit("clicked")
            wish_dialog = getattr(window, "_reader_intent_dialog", None)
            assert isinstance(wish_dialog, Adw.MessageDialog)
            wish_dialog.emit("response", "remove")
            assert intent()["desired_state"] == "remove" and final.exists()
            dialog = sync_dialog()
            assert "1 retrait(s)" in dialog.get_body()
            assert final.exists()
            dialog.emit("response", "sync")
            wait(lambda: not getattr(window, "_reader_sync_active", False))
            assert not final.exists() and intent()["observed_state"] == "missing"
            assert local.read_bytes() == payload and third.read_bytes() == b"untouched"
            assert db.device_placements(device_id="reader-b") == []
            report["checks"]["native_confirmed_remove_preserves_source_and_third_party"] = True

            # Owner-approved USB fixture: detect two disposable filesystem
            # roots instead of emulating a physical block device/udev. Reuse
            # the real local inode/device/mount proof only inside this test;
            # actual USB bindings, Core operations and SQLite stay unchanged.
            from cifereca.core import device_usb_mount as mount_ops
            from cifereca.core import device_usb_discovery as discovery
            real_attest = mount_ops.observe_linux_usb_mount
            usb_roots, local_proofs, connected = {}, {}, set()
            for owner, reader in (("alice", "reader-a"), ("bob", "reader-b")):
                volume_root = work / ("synthetic-volume-" + owner)
                (volume_root / "Books" / "Cifereca").mkdir(parents=True)
                info = volume_root.stat()
                fp = "uuid-" + owner + "-0001"
                usb_roots[reader] = (owner, fp, volume_root)
                local_proofs[reader] = dict(reader_transport="local",
                    local_root=str(volume_root), local_inode=info.st_ino,
                    local_device=info.st_dev, usb_fingerprint=fp)
                db.device_usb_reader_formats_set(identity_id=owner, device_id=reader,
                    usb_fingerprint=fp, supported_formats=("epub",))

            def attest(*, identity_id, binding, mountpoint):
                reader = binding.get("device_id")
                if reader not in usb_roots:
                    return real_attest(identity_id=identity_id, binding=binding,
                                       mountpoint=mountpoint)
                owner, fp, volume_root = usb_roots[reader]
                if (reader not in connected or identity_id != owner
                        or binding.get("identity_id") != owner
                        or binding.get("enabled") is not True
                        or binding.get("metadata", {}).get("usb_fingerprint") != fp
                        or mountpoint != volume_root):
                    return None
                # A fixture-only view of the binding: never persisted. The
                # real proof opens the actual filesystem without following
                # symlinks and checks its pinned inode and device.
                local_binding = dict(binding, metadata=local_proofs[reader])
                proof = real_attest(identity_id=owner, binding=local_binding,
                                    mountpoint=volume_root)
                return replace(proof, filesystem="ext4") if proof is not None else None

            def discover():
                results = []
                for reader in sorted(connected):
                    owner, fp, volume_root = usb_roots[reader]
                    proof = attest(identity_id=owner, binding=db.device_binding_get(reader),
                                   mountpoint=volume_root)
                    if proof is not None:
                        results.append(discovery.MountedUsbVolume(fp, volume_root,
                            proof.filesystem, "Synthetic filesystem", None, proof.major,
                            proof.minor, proof.mount_id))
                return tuple(results)

            def switch_profile(owner, item):
                window.service.select_identity(owner)
                window._identity_context_generation += 1
                window._account_context_generation += 1
                window.navigate("readers")
                window._library_entries_cache = [item]
                window._render_readers()
                assert window._reader_scope_identity == owner

            def connect_usb(reader):
                phase[0] = "native_usb_discovery"
                owner, fp, volume_root = usb_roots[reader]
                assert production["discover"](storage=db, identity_id=owner,
                    device_id=reader, fingerprint=fp) is None
                connected.add(reader)
                select_reader(reader)
                volume = production["discover"](storage=db, identity_id=owner,
                    device_id=reader, fingerprint=fp)
                assert type(volume) is discovery.MountedUsbVolume
                assert volume.mountpoint == volume_root
                # Native directory setup is already exercised on the local
                # reader above; these USB fixtures start with a managed dir.
                other = volume_root / "Books" / "Cifereca" / "third-party.txt"
                other.write_bytes(b"untouched " + reader.encode("ascii"))
                return other

            def sync_real(reader):
                phase[0] = "native_usb_preview"
                dialog = sync_dialog()
                assert "1 envoi(s)" in dialog.get_body()
                results, copies = [], []
                def capture_copy(**kwargs):
                    outcome = original_copy(**kwargs)
                    copies.append(outcome)
                    return outcome
                def capture_admit(broker, **kwargs):
                    operation = original_admit(broker, **kwargs)
                    def execute():
                        result = operation()
                        results.append(result)
                        return result
                    return execute
                with (patch.object(type(facade), "admit_delta", capture_admit),
                      patch.object(sync_engine, "execute_usb_library_copy", capture_copy)):
                    phase[0] = "native_usb_admission"
                    dialog.emit("response", "sync")
                    assert window._reader_sync_active
                    phase[0] = "native_usb_copy_result"
                    try:
                        wait(lambda: not getattr(window, "_reader_sync_active", False), seconds=12)
                    except AssertionError:
                        report["closed_copy_result"] = ("copy_delivery_timeout" if results
                                                        else "copy_worker_timeout")
                        raise
                if len(copies) != 1:
                    report["closed_copy_result"] = "copy_result_count_mismatch"
                elif type(copies[0]) is not UsbCopyExecution:
                    report["closed_copy_result"] = "copy_result_unclassified"
                elif copies[0].status != "completed":
                    reason = copies[0].reason
                    report["closed_copy_result"] = (reason if reason in COPY_VERDICTS
                                                    else "copy_result_unclassified")
                elif results != [UsbSyncResult("completed", copied=1, observed=True)]:
                    report["closed_copy_result"] = "copy_sync_receipt_mismatch"
                assert len(copies) == 1 and type(copies[0]) is UsbCopyExecution
                assert copies[0].status == "completed"
                assert results == [UsbSyncResult("completed", copied=1, observed=True)]
                phase[0] = "native_usb_observation"
                saved = db.device_placement_get(reader, artifact_id)
                target = usb_roots[reader][2] / saved["target_relative_path"]
                assert saved["observed_state"] == "present"
                assert target.read_bytes() == payload
                return target

            with ExitStack() as stack:
                # The functions are imported by value in these Core modules.
                # All other code, including independent target scan, executes.
                for name in (
                    "device_usb_mount", "device_usb_execution_broker",
                    "device_usb_managed_scan", "device_usb_target_preflight",
                    "device_usb_live_source", "device_usb_removal_execute",
                    "device_usb_removal_review",
                ):
                    module = import_module("cifereca.core." + name)
                    stack.enter_context(patch.object(module, "observe_linux_usb_mount", attest))
                stack.enter_context(patch.object(discovery, "discover_linux_usb_volumes", discover))

                phase[0] = "native_usb_a"
                third_a = connect_usb("reader-a")
                assert db.device_placement_get("reader-a", artifact_id)["desired_state"] == "keep"
                usb_a = sync_real("reader-a")
                assert third_a.read_bytes() == b"untouched reader-a"
                assert not final.exists() and intent()["observed_state"] == "missing"
                report["checks"]["native_reconnected_usb_a_real_copy"] = True

                phase[0] = "native_usb_b"
                phase[0] = "native_usb_b_source"
                bob_root = configured_identity_roots(core.config, "bob")["bob"][0]
                bob_root.mkdir(parents=True, exist_ok=True)
                bob_source = bob_root / "fixture.epub"
                bob_source.write_bytes(payload)
                bob_placement = db.placement_put({
                    "artifact_id": artifact_id, "path": str(bob_source),
                    "identity_id": "bob", "provider_id": "local",
                    "source_item_id": "bob-reader-book", "state": "present", "metadata": {},
                })
                bob_entry = dict(entry, key="bob-reader-book", identity_id="bob",
                                 placement_id=bob_placement)
                phase[0] = "native_usb_b_profile"
                switch_profile("bob", bob_entry)
                assert window._reader_device_ids == ("reader-b",)
                select_reader("reader-b")
                assert db.placement_get(bob_placement)["identity_id"] == "bob"
                phase[0] = "native_usb_b_naming_dialog"
                header("reader-directory-config").emit("clicked")
                naming_dialog = dialog_with_heading("Dossier géré de la liseuse")
                naming_dialog.get_extra_child().get_last_child().set_selected(1)
                phase[0] = "native_usb_b_naming_commit"
                naming_dialog.emit("response", "save")
                assert db.device_binding_get("reader-b")["metadata"]["reader_target_naming"] == "source"
                phase[0] = "native_usb_b_menu"
                menu, choices = menu_choices(bob_entry)
                assert "usb-intent:keep:reader-b" in choices
                assert "usb-intent:keep:reader-a" not in choices
                assert "usb-intent:keep:" + device not in choices
                wait(choices["usb-intent:keep:reader-b"].get_mapped)
                phase[0] = "native_usb_b_wish"
                choices["usb-intent:keep:reader-b"].emit("clicked")
                bob_wish = db.device_placement_get("reader-b", artifact_id)
                assert bob_wish is not None
                assert bob_wish["desired_state"] == "keep" and bob_wish["observed_state"] == "unknown"
                phase[0] = "native_usb_b_reservation"
                assert bob_wish["target_relative_path"] == "Books/Cifereca/fixture--" + digest + ".epub"
                menu.get_popover().popdown()
                window.navigate("readers")
                window._library_entries_cache = [bob_entry]
                window._render_readers()
                third_b = connect_usb("reader-b")
                usb_b = sync_real("reader-b")
                assert usb_a.read_bytes() == usb_b.read_bytes() == payload
                report["checks"]["native_usb_b_real_copy_same_sha_other_profile"] = True

                phase[0] = "native_usb_isolation"
                switch_profile("alice", entry)
                select_reader("reader-a")
                assert window.readers_page.select_key("reader-book")
                # Remove A's real USB copy through both GTK confirmations,
                # leaving the same SHA on B and all local/third-party files.
                remove = wait(mapped_intent_button)
                remove.emit("clicked")
                wish_dialog = wait(lambda: getattr(window, "_reader_intent_dialog", None))
                wish_dialog.emit("response", "remove")
                assert usb_a.exists() and usb_b.exists()
                dialog = sync_dialog()
                assert "1 retrait(s)" in dialog.get_body()
                assert any(isinstance(widget, Gtk.Label) and digest + ".epub" in widget.get_text()
                           for widget in walk(dialog.get_extra_child()))
                dialog.emit("response", "sync")
                wait(lambda: not getattr(window, "_reader_sync_active", False))
                assert not usb_a.exists()
                assert db.device_placement_get("reader-a", artifact_id)["observed_state"] == "missing"
                assert usb_b.read_bytes() == bob_source.read_bytes() == local.read_bytes() == payload
                assert db.device_placement_get("reader-b", artifact_id)["observed_state"] == "present"
                assert third_a.read_bytes() == b"untouched reader-a"
                assert third_b.read_bytes() == b"untouched reader-b"
                assert third.read_bytes() == b"untouched"
                report["checks"]["native_three_real_destinations_and_cross_profile_removal_isolation"] = True
        finally:
            manager.services = original_services

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
