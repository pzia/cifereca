"""#4985: GTK selected-book removal through public Core broker only.

No physical USB I/O in the fake widget harness. Real GTK widths remain
covered by reader_target_refresh_probe; these tests pin the new asynchronous
two-confirmation execution journey, identity ABA and Core refusal cases.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest

from cifereca.core.device_usb_discovery import MountedUsbVolume
from cifereca.core.device_usb_dry_run import UsbDryRun
from cifereca.core.device_usb_preview import UsbPreviewRow
from cifereca.core.device_usb_removal_review import UsbRemovalConsent
from cifereca.core.device_usb_sync_engine import UsbSyncResult
from cifereca.gui import reader_usb_execute_actions as controller
from tests.test_p1_2854_readers_master_detail_page import environment


class Runner:
    def __init__(self):
        self.jobs = []
        self.history = []

    def submit(self, label, work, *, ok, err, control, **options):
        self.history.append((label, options["kind"], options["origin"]))
        self.jobs.append((work, ok, err))

    def advance(self):
        work, ok, err = self.jobs.pop(0)
        try:
            result = work()
        except Exception as error:
            err(error)
        else:
            ok(result)


class Dialog:
    def __init__(self):
        self.responses = {}
        self.callbacks = {}
        self.presented = False
        self.close_response = None

    def add_response(self, key, label):
        self.responses[key] = label

    def set_close_response(self, key):
        self.close_response = key

    def connect(self, name, callback):
        self.callbacks[name] = callback

    def present(self):
        self.presented = True

    def answer(self, response):
        self.callbacks["response"](self, response)


class FakeBroker:
    def __init__(self, report):
        self.report = report
        self.previews = []
        self.execution = []
        self.copy_operation = "a" * 32
        self.copy_calls = []
        self.outcome = UsbSyncResult("completed", removed=1, observed=True)

    def preview(self, **kwargs):
        self.previews.append(kwargs)
        return self.report

    def completed_copy_operation(self, **kwargs):
        self.copy_calls.append(kwargs)
        return self.copy_operation

    def admit_removal(self, **kwargs):
        # The unit harness models the new Core admission seam. Real SQLite,
        # mount and source-epoch revocations are covered in broker tests.
        assert kwargs["expected_profile_epoch"] == self.service._account_context_epoch
        assert kwargs["confirmation"].copy_operation_id == self.copy_operation
        initial_source_epoch = getattr(self.service, "_source_context_epoch", 0)

        def check():
            consent = kwargs["confirmation"]
            binding = self.storage.device_binding_get(consent.device_id)
            intent = self.storage.device_placement_get(consent.device_id, consent.artifact_id)
            if (getattr(self.service, "_source_context_epoch", 0) != initial_source_epoch
                    or self.volume["volume"] is None
                    or self.volume["volume"].selection_key() != kwargs["mount_key"]
                    or self.copy_operation != consent.copy_operation_id
                    or not binding["enabled"] or intent["desired_state"] != "remove"):
                raise ValueError("usb_reader_scope_revoked")

        def work():
            check()
            self.execution.append({**kwargs, "check_cancelled": check})
            return self.outcome
        return work


@pytest.fixture
def rig(tmp_path, monkeypatch):
    db, entries, host, _ = environment(tmp_path)
    book = entries[0]
    # The shared read-only reader fixture deliberately starts disabled. An
    # explicit execution test must enable the *exact* configured reader.
    db.device_usb_binding_declare(
        identity_id="alice", device_id="reader-alice",
        label="Lecteur Alice", usb_fingerprint="uuid-alice-0001",
        enabled=True,
    )
    digest = book["artifact_id"].removeprefix("sha256:")
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=book["artifact_id"], desired_state="keep",
    )
    target = "Books/Cifereca/" + digest + ".epub"
    db.device_usb_target_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=book["artifact_id"], target_relative_path=target,
    )
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=book["artifact_id"], desired_state="remove",
    )
    mount = tmp_path / "mounted-reader"
    volume = MountedUsbVolume(
        "uuid-alice-0001", mount, "vfat", "Reader synthetic",
        1024 * 1024 * 100, 8, 5, 71,
    )
    from cifereca.gui import reader_usb_preview_actions as preview
    selected = {"volume": volume}
    monkeypatch.setattr(
        preview, "_discover_mounted_binding",
        lambda **kwargs: selected["volume"],
    )
    row = UsbPreviewRow(
        book["artifact_id"], "remove", "present", "unknown", None,
        "remove_candidate", "managed_removal_requires_confirmation",
    )
    report = UsbDryRun(
        "alice", "reader-alice", "scanned", True, int(time.time()), (row,),
    )
    backend = FakeBroker(report)
    backend.service, backend.storage, backend.volume = host.service, db, selected
    host.plugin_manager = NS(
        broker=lambda name: backend if name == "reader.usb" else None,
    )
    host.runner = Runner()
    host._identity_context_generation = 0
    host._account_context_generation = 0
    host.service._account_context_epoch = 1
    host._route_display_token = object()
    host._render_readers()
    host.selected_row = host.readers_page.rows[0]
    host.readers_page.selection = NS(get_selected_item=lambda: host.selected_row)
    errors = []
    host.show_error = lambda message, title: errors.append((message, title))
    dialogs = []
    adw = NS(
        MessageDialog=NS(new=lambda *args: dialogs.append(Dialog()) or dialogs[-1]),
    )
    actions = []
    detail = NS(add_actions=lambda buttons: actions.extend(buttons))
    candidate = host._reader_items[host.selected_row.key]
    assert controller.append_reader_usb_execute_action(
        host, detail, candidate, Gtk=host._reader_gtk, Adw=adw,
    ) is True
    assert len(actions) == 1
    assert actions[0]._cifereca_action_id == "reader-usb-execute-remove"
    return NS(
        db=db, book=book, host=host, backend=backend, volume=volume,
        selected=selected, dialogs=dialogs,
        button=actions[0], errors=errors,
    )


def click(rig):
    rig.button.callbacks["clicked"](rig.button)


def test_render_and_clicked_do_not_scan_usb_or_unlink(rig):
    r = rig
    local = Path(r.db.placement_get(r.book["placement_id"])["path"])
    original = local.read_bytes()
    assert r.backend.previews == [] and r.backend.execution == []
    assert r.host.runner.jobs == []
    click(r)
    assert len(r.host.runner.jobs) == 1
    assert r.host.runner.history == [
        ("Vérifier un retrait USB géré…", "maintenance-inspection", "user"),
    ]
    assert r.backend.previews == [] and r.backend.execution == []
    assert r.dialogs == []
    assert local.read_bytes() == original


def test_cancelled_confirmation_never_calls_core_executor(rig):
    click(rig)
    rig.host.runner.advance()
    assert len(rig.backend.previews) == 1
    assert len(rig.backend.copy_calls) >= 1
    assert len(rig.dialogs) == 1 and rig.dialogs[0].presented
    assert rig.dialogs[0].close_response == "cancel"
    assert "delete" in rig.dialogs[0].responses
    assert rig.backend.execution == []
    rig.dialogs[0].answer("cancel")
    assert rig.backend.execution == []
    assert rig.host.runner.jobs == []


def test_confirmed_removal_has_distinct_worker_and_exact_consent(rig):
    click(rig)
    rig.host.runner.advance()
    rig.dialogs[0].answer("delete")
    assert rig.backend.execution == []
    assert len(rig.host.runner.jobs) == 1
    rig.host.runner.advance()
    assert len(rig.backend.execution) == 1
    invocation = rig.backend.execution[0]
    consent = invocation["confirmation"]
    assert type(consent) is UsbRemovalConsent
    assert consent.identity_id == "alice"
    assert consent.device_id == "reader-alice"
    assert consent.usb_fingerprint == "uuid-alice-0001"
    assert consent.artifact_id == rig.book["artifact_id"]
    assert consent.target_relative_path == (
        "Books/Cifereca/" + rig.book["artifact_id"][7:] + ".epub"
    )
    assert consent.copy_operation_id == "a" * 32
    assert len(consent.operation_id) == 32
    assert consent.expires_at - consent.confirmed_at == 60
    assert invocation["mountpoint"] == rig.volume.mountpoint
    assert invocation["expected_profile_epoch"] == 1
    assert callable(invocation["check_cancelled"])
    # Repainting the detail does not revoke the Core admission.
    assert invocation["check_cancelled"]() is None
    assert "Retrait USB supervisé terminé" in rig.host._reader_status_label.label
    assert rig.db.device_placement_get(
        "reader-alice", rig.book["artifact_id"],
    )["desired_state"] == "remove"


@pytest.mark.parametrize("change", [
    "profile", "account_epoch", "identity_generation", "account_generation",
    "route", "device", "selection", "snapshot", "closed",
])
def test_late_review_callback_cannot_open_dialog_or_execute(rig, change):
    click(rig)
    host = rig.host
    if change == "profile":
        host.service.current_identity.identity_id = "bob"
    elif change == "account_epoch":
        host.service._account_context_epoch += 2
    elif change == "identity_generation":
        host._identity_context_generation += 1
    elif change == "account_generation":
        host._account_context_generation += 1
    elif change == "route":
        host._route_display_token = object()
    elif change == "device":
        host._reader_selected_device_id = "reader-alice-2"
    elif change == "selection":
        host.selected_row = NS(key="another")
    elif change == "snapshot":
        host._reader_library_snapshot = object()
    else:
        host._closed = True
    host.runner.advance()
    assert rig.dialogs == []
    assert rig.backend.execution == []
    assert host.runner.jobs == []


@pytest.mark.parametrize("change", [
    "profile", "account_epoch", "identity_generation", "account_generation",
    "route", "device", "selection", "snapshot", "closed",
])
def test_confirmation_after_scope_change_never_runs_executor(rig, change):
    click(rig)
    rig.host.runner.advance()
    assert len(rig.dialogs) == 1
    host = rig.host
    if change == "profile":
        host.service.current_identity.identity_id = "bob"
    elif change == "account_epoch":
        host.service._account_context_epoch += 2
    elif change == "identity_generation":
        host._identity_context_generation += 1
    elif change == "account_generation":
        host._account_context_generation += 1
    elif change == "route":
        host._route_display_token = object()
    elif change == "device":
        host._reader_selected_device_id = "reader-alice-2"
    elif change == "selection":
        host.selected_row = NS(key="another")
    elif change == "snapshot":
        host._reader_library_snapshot = object()
    else:
        host._closed = True
    rig.dialogs[0].answer("delete")
    assert rig.backend.execution == []
    assert host.runner.jobs == []


@pytest.mark.parametrize("change", [
    "mount_swap", "receipt_replacement", "wish_revoked", "device_disabled",
])
def test_worker_revalidates_mount_copy_receipt_and_intent(rig, monkeypatch, change):
    click(rig)
    rig.host.runner.advance()
    rig.dialogs[0].answer("delete")
    if change == "mount_swap":
        rig.selected["volume"] = replace(rig.volume, mount_id=72)
    elif change == "receipt_replacement":
        rig.backend.copy_operation = "c" * 32
    elif change == "wish_revoked":
        rig.db.device_usb_intent_set(
            identity_id="alice", device_id="reader-alice",
            artifact_id=rig.book["artifact_id"], desired_state="keep",
        )
    else:
        rig.db.device_usb_binding_declare(
            identity_id="alice", device_id="reader-alice", label="Lecteur Alice",
            usb_fingerprint="uuid-alice-0001", enabled=False,
        )
    rig.host.runner.advance()
    assert rig.backend.execution == []
    assert "Retrait USB non confirmé" in rig.host._reader_status_label.label


@pytest.mark.parametrize("change", [
    "no_volume", "wrong_report", "unscanned", "other_action", "wrong_reason",
    "missing_receipt",
])
def test_review_has_no_positive_without_matching_physical_scan_and_copy(rig, monkeypatch, change):
    if change == "no_volume":
        rig.selected["volume"] = None
    elif change == "wrong_report":
        rig.backend.report = replace(rig.backend.report, identity_id="bob")
    elif change == "unscanned":
        rig.backend.report = replace(rig.backend.report, scan_attested=False)
    elif change == "other_action":
        rig.backend.report = replace(
            rig.backend.report, rows=(replace(rig.backend.report.rows[0],
                                               action="verify"),),
        )
    elif change == "wrong_reason":
        rig.backend.report = replace(
            rig.backend.report, rows=(replace(rig.backend.report.rows[0],
                                               reason="source_unverified"),),
        )
    else:
        rig.backend.copy_operation = None
    click(rig)
    rig.host.runner.advance()
    assert rig.dialogs == []
    assert rig.backend.execution == []
    assert "Retrait USB non confirmé" in rig.host._reader_status_label.label


def test_broker_refusal_is_not_reported_as_device_success(rig):
    rig.backend.outcome = UsbSyncResult("partial", failed=1, observed=False)
    click(rig)
    rig.host.runner.advance()
    rig.dialogs[0].answer("delete")
    rig.host.runner.advance()
    assert len(rig.backend.execution) == 1
    assert "Retrait USB non confirmé" in rig.host._reader_status_label.label
    assert "terminé" not in rig.host._reader_status_label.label


def test_profile_aba_after_user_confirmed_is_pinned_in_core_worker(rig):
    click(rig)
    rig.host.runner.advance()
    assert len(rig.dialogs) == 1
    rig.dialogs[0].answer("delete")
    # The worker retains the original permission despite navigation ABA.
    rig.host.service._account_context_epoch = 3
    rig.host.runner.advance()
    assert len(rig.backend.execution) == 1
    assert rig.backend.execution[0]["expected_profile_epoch"] == 1
    assert "terminé" not in rig.host._reader_status_label.label
    assert rig.db.device_placement_get(
        "reader-alice", rig.book["artifact_id"],
    )["desired_state"] == "remove"


@pytest.mark.parametrize("missing", ["plugin_manager", "broker", "copy_facet", "disabled"])
def test_missing_backend_or_disabled_reader_never_offers_destructive_action(
    rig, missing,
):
    host = rig.host
    if missing == "plugin_manager":
        host.plugin_manager = None
    elif missing == "broker":
        host.plugin_manager = NS(broker=lambda _: None)
    elif missing == "copy_facet":
        rig.backend.completed_copy_operation = None
    else:
        rig.db.device_usb_binding_declare(
            identity_id="alice", device_id="reader-alice",
            label="Lecteur Alice", usb_fingerprint="uuid-alice-0001",
            enabled=False,
        )
    buttons = []
    candidate = host._reader_items[host.selected_row.key]
    offered = controller.append_reader_usb_execute_action(
        host, NS(add_actions=lambda items: buttons.extend(items)),
        candidate, Gtk=host._reader_gtk,
    )
    assert offered is False and buttons == []
    assert rig.backend.previews == []
    assert rig.backend.execution == []
    assert host.runner.jobs == []


def test_only_latest_of_two_overlapping_explicit_requests_can_prompt_user(rig):
    click(rig)
    click(rig)
    assert len(rig.host.runner.jobs) == 2
    rig.host.runner.advance()
    assert rig.dialogs == []
    rig.host.runner.advance()
    assert len(rig.dialogs) == 1
    rig.dialogs[0].answer("cancel")
    assert rig.backend.execution == []


def test_replacing_selected_detail_only_revokes_late_usb_publication(rig):
    click(rig)
    rig.host.runner.advance()
    assert len(rig.dialogs) == 1
    rig.dialogs[0].answer("delete")
    # Gtk.Widget:root notifies when the selected detail is detached.
    rig.button.get_root = lambda: None
    rig.button.callbacks["notify::root"](rig.button, None)
    rig.host.runner.advance()
    assert len(rig.backend.execution) == 1
    assert "terminé" not in rig.host._reader_status_label.label
    assert rig.db.device_placement_get(
        "reader-alice", rig.book["artifact_id"],
    )["desired_state"] == "remove"


def test_core_cancel_callback_ignores_visual_device_aba_but_vetoes_source_revocation(rig):
    # A partial operation does not repaint. Visual ABA is not revocation;
    # real configuration/source change remains a business veto.
    rig.backend.outcome = UsbSyncResult("partial", failed=1, observed=False)
    click(rig)
    rig.host.runner.advance()
    rig.dialogs[0].answer("delete")
    rig.host.runner.advance()
    assert len(rig.backend.execution) == 1
    cancel = rig.backend.execution[0]["check_cancelled"]
    assert cancel() is None
    rig.host._reader_selection_generation += 2
    assert cancel() is None
    rig.host.service._source_context_epoch = 1
    with pytest.raises(ValueError, match="usb_reader_scope_revoked"):
        cancel()


def test_worker_checks_original_selection_before_usb_scan(rig):
    click(rig)
    rig.host._reader_selection_generation += 1
    rig.host.runner.advance()
    assert rig.backend.previews == []
    assert rig.backend.execution == []
    assert rig.dialogs == []


def test_unroot_before_worker_discovery_prevents_even_usb_preview(rig):
    click(rig)
    assert len(rig.host.runner.jobs) == 1
    rig.button.get_root = lambda: None
    rig.button.callbacks["notify::root"](rig.button, None)
    rig.host.runner.advance()
    assert rig.backend.previews == []
    assert rig.backend.copy_calls == []
    assert rig.backend.execution == []
    assert rig.dialogs == []


def test_old_unroot_cannot_revoke_new_selected_control(rig):
    first = rig.button
    click(rig)
    # Simulate a replacement detail with a distinct new active control.
    second = object()
    nonce = object()
    rig.host._reader_usb_execute_control = second
    rig.host._reader_usb_execute_nonce = nonce
    first.get_root = lambda: None
    first.callbacks["notify::root"](first, None)
    assert rig.host._reader_usb_execute_nonce is nonce
