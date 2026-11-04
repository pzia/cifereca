"""P1 #2854: explicit reader USB examination is user-triggered and inert.

Native chooser + TaskRunner are headless fakes; actual Core report is typed.
No hardware, mount operations, file writes or source/network requests.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.device_usb_dry_run import UsbDryRun
from cifereca.core.device_usb_preview import UsbPreviewRow
from cifereca.gui.reader_usb_preview_actions import (
    _summary, request_reader_usb_preview,
)
from cifereca.identities import Identity
from cifereca.storage import Storage
from tests.test_p1_2854_readers_master_detail_page import environment


class FakeLabel:
    def __init__(self):
        self.text = ""

    def set_label(self, value):
        self.text = value


class FakeFile:
    def __init__(self, path):
        self.path = path

    def get_path(self):
        return self.path


class FakeNative:
    created = []

    def __init__(self):
        self.callback = None
        self.file = None
        self.shown = False
        self.destroyed = False
        self.__class__.created.append(self)

    @classmethod
    def new(cls, title, parent, action, accept, cancel):
        assert "volume USB" in title
        assert action == "select_folder"
        assert accept == "Examiner" and cancel == "Annuler"
        return cls()

    def get_file(self):
        return self.file

    def connect(self, signal, callback):
        assert signal == "response"
        self.callback = callback

    def show(self):
        self.shown = True

    def destroy(self):
        self.destroyed = True

    def respond(self, path, response="accept"):
        self.file = FakeFile(path) if path is not None else None
        assert self.callback is not None
        self.callback(self, response)


class FakeRunner:
    def __init__(self):
        self.calls = []

    def submit(self, label, fn, ok=None, err=None, **options):
        self.calls.append((label, fn, ok, err, options))


FAKE_GTK = NS(
    FileChooserNative=FakeNative,
    FileChooserAction=NS(SELECT_FOLDER="select_folder"),
    ResponseType=NS(ACCEPT="accept"),
)


def rig(tmp_path):
    db = Storage(tmp_path / "store.sqlite3")
    for identity in ("alice", "bob"):
        db.identity_put(Identity(identity, identity.title()))
    db.device_usb_binding_declare(
        identity_id="alice", device_id="reader-alice",
        label="Reader", usb_fingerprint="uuid-alice-0001", enabled=True,
    )
    service = NS(current_identity=NS(identity_id="alice"), storage=db)
    library = tmp_path / "library"
    config = NS(
        identities=lambda: [
            {"id": "alice", "directory_name": "alice"},
            {"id": "bob", "directory_name": "bob"},
        ],
        get=lambda key, default=None: str(library) if key == "library.directory"
        else default,
    )
    root = library / "alice"
    root.mkdir(parents=True)
    route = object()
    window = NS(
        service=service, config=config,
        runner=FakeRunner(), _closed=False,
        _reader_selected_device_id="reader-alice",
        _reader_scope_identity="alice",
        _identity_context_generation=3,
        _account_context_generation=1,
        _reader_selection_generation=0,
        _route_display_token=route,
        _reader_status_label=FakeLabel(),
    )
    return db, window, root


def report(*, status="scanned", attested=True, rows=()):
    return UsbDryRun(
        identity_id="alice", device_id="reader-alice",
        status=status, scan_attested=attested,
        observed_at=1800000000 if attested else None,
        rows=rows,
    )


def row(action="upload_candidate", *, executable=False):
    return UsbPreviewRow(
        artifact_id="sha256:" + "a" * 64,
        desired="keep", observed="missing",
        last_known="unknown", last_observed_at=None,
        action=action, reason="read_only_advisory",
        executable=executable,
    )


def test_reader_page_wires_separate_inspection_from_ordinary_refresh(tmp_path):
    db, entries, host, actions = environment(tmp_path)
    # The built page is inert: no chooser or task has been allocated.
    buttons = host.readers_page.header_actions.children
    assert len(buttons) == 5
    assert [getattr(button, "_cifereca_action_id", None) for button in buttons[1:]] == [
        "reader-usb-dry-run", "reader-usb-pair", "reader-usb-formats",
        "reader-usb-activation",
    ]
    assert all("clicked" in button.callbacks for button in buttons[1:])
    assert getattr(host, "_reader_usb_preview_chooser", None) is None
    host._render_readers()
    assert getattr(host, "_reader_usb_preview_chooser", None) is None
    assert db.device_placements() == []


def test_navigation_never_scans_and_explicit_click_only_opens_chooser(tmp_path):
    db, window, root = rig(tmp_path)
    button = object()
    request_reader_usb_preview(window, button, Gtk=FAKE_GTK)
    dialog = window._reader_usb_preview_chooser
    assert dialog.shown is True
    assert window.runner.calls == []
    assert db.device_placements() == []
    dialog.respond(None, response="cancel")
    assert dialog.destroyed is True
    assert window.runner.calls == []
    assert window._reader_usb_preview_chooser is None


def test_selected_mountpoint_is_passed_only_to_background_worker(
    tmp_path, monkeypatch,
):
    db, window, root = rig(tmp_path)
    button = object()
    import cifereca.core.device_usb_dry_run as worker
    submitted = []

    def inspect(**args):
        submitted.append(args)
        return report(rows=(row("upload_candidate"), row("blocked")))

    monkeypatch.setattr(worker, "inspect_usb_dry_run", inspect)
    request_reader_usb_preview(window, button, Gtk=FAKE_GTK)
    dialog = window._reader_usb_preview_chooser
    selected_mount = tmp_path / "synthetic-reader"
    selected_mount.mkdir()
    dialog.respond(str(selected_mount))
    assert dialog.destroyed is True
    assert submitted == []
    assert window._reader_usb_preview_chooser is None
    assert window._reader_status_label.text.startswith("Examen USB en cours")
    assert len(window.runner.calls) == 1
    label, fn, ok, err, opts = window.runner.calls[0]
    assert label.startswith("Examiner")
    assert opts["control"] is button
    assert opts["origin"] == "user"
    assert opts["kind"] == "maintenance-inspection"
    assert fn is not None and callable(ok) and callable(err)
    result = fn()
    assert result.scan_attested is True
    assert len(submitted) == 1
    assert submitted[0]["identity_id"] == "alice"
    assert submitted[0]["device_id"] == "reader-alice"
    assert submitted[0]["identity_root"] == root
    assert submitted[0]["mountpoint"] == selected_mount
    assert submitted[0]["storage"] is db
    ok(result)
    assert "1 envoi(s) à examiner" in window._reader_status_label.text
    assert "1 bloqué(s)" in window._reader_status_label.text
    assert "Aucun transfert ni retrait" in window._reader_status_label.text


@pytest.mark.parametrize("malformed", [
    None, "", "relative/path", "/", "../escape",
    "/tmp/../fake-mount", "/tmp/usb\u0000private",
])
def test_invalid_chooser_selection_never_enqueues_usb_job(tmp_path, malformed):
    _db, window, root = rig(tmp_path)
    request_reader_usb_preview(window, object(), Gtk=FAKE_GTK)
    window._reader_usb_preview_chooser.respond(malformed)
    assert window.runner.calls == []
    assert "Aucun transfert" in window._reader_status_label.text


@pytest.mark.parametrize("change", [
    "identity", "generation", "account_generation", "device",
    "route", "closed", "binding", "fingerprint",
    "selection_generation", "root_changed",
])
def test_stale_chooser_cannot_enqueue_any_work(tmp_path, change):
    db, window, root = rig(tmp_path)
    request_reader_usb_preview(window, object(), Gtk=FAKE_GTK)
    dialog = window._reader_usb_preview_chooser
    if change == "identity":
        window.service.current_identity = NS(identity_id="bob")
    elif change == "generation":
        window._identity_context_generation += 1
    elif change == "account_generation":
        window._account_context_generation += 1
    elif change == "device":
        window._reader_selected_device_id = "other-reader"
    elif change == "route":
        window._route_display_token = object()
    elif change == "closed":
        window._closed = True
    elif change == "selection_generation":
        window._reader_selection_generation += 2
    elif change == "root_changed":
        window.config.get = lambda key, default=None: (
            str(tmp_path / "migrated-library")
            if key == "library.directory" else default
        )
    elif change == "binding":
        row = db.device_binding_get("reader-alice")
        db.device_binding_put({**row, "identity_id": "bob"})
    else:
        row = db.device_binding_get("reader-alice")
        db.device_binding_put({
            **row, "metadata": {"usb_fingerprint": "uuid-swapped-0001"},
        })
    dialog.respond(str(tmp_path))
    assert dialog.destroyed
    assert window.runner.calls == []
    assert window._reader_usb_preview_chooser is None


@pytest.mark.parametrize("change", [
    "identity", "generation", "device", "route", "binding",
    "selection_generation", "root_changed",
])
def test_stale_worker_completion_never_updates_next_profile(tmp_path, change):
    db, window, root = rig(tmp_path)
    request_reader_usb_preview(window, object(), Gtk=FAKE_GTK)
    window._reader_usb_preview_chooser.respond(str(tmp_path))
    _, fn, ok, err, _ = window.runner.calls[0]
    window._reader_status_label.set_label("Bob utilise la page")
    if change == "identity":
        window.service.current_identity = NS(identity_id="bob")
    elif change == "generation":
        window._identity_context_generation += 1
    elif change == "device":
        window._reader_selected_device_id = "some-other-device"
    elif change == "route":
        window._route_display_token = object()
    elif change == "selection_generation":
        window._reader_selection_generation += 2
    elif change == "root_changed":
        window.config.get = lambda key, default=None: (
            str(tmp_path / "migrated-library")
            if key == "library.directory" else default
        )
    else:
        binding_row = db.device_binding_get("reader-alice")
        db.device_binding_put({**binding_row, "identity_id": "bob"})
    ok(report(rows=(row(),)))
    err(RuntimeError("PRIVATE_TOKEN secret"))
    assert window._reader_status_label.text == "Bob utilise la page"


def test_only_one_outstanding_chooser_has_the_right_to_submit(tmp_path):
    db, window, root = rig(tmp_path)
    request_reader_usb_preview(window, object(), Gtk=FAKE_GTK)
    first = window._reader_usb_preview_chooser
    request_reader_usb_preview(window, object(), Gtk=FAKE_GTK)
    second = window._reader_usb_preview_chooser
    first.respond(str(tmp_path))
    assert window.runner.calls == []
    second.respond(str(tmp_path))
    assert len(window.runner.calls) == 1


def test_disabled_or_unbound_reader_cannot_choose_usb_folder(tmp_path):
    db, window, root = rig(tmp_path)
    db.device_usb_binding_declare(
        identity_id="alice", device_id="reader-alice",
        label="Reader", usb_fingerprint="uuid-alice-0001", enabled=False,
    )
    request_reader_usb_preview(window, object(), Gtk=FAKE_GTK)
    assert window.runner.calls == []
    assert getattr(window, "_reader_usb_preview_chooser", None) is None
    assert "Aucun scan" in window._reader_status_label.text


def test_summary_never_mints_transfer_rights_or_leaks_raw_fields():
    assert "Aucun transfert" in _summary(
        report(rows=(row("upload_candidate"), row("skip"), row("blocked"))))
    assert "Support USB non attesté" in _summary(
        report(status="device_unverified", attested=False))
    assert "indisponible" in _summary(
        report(rows=(row("remove_candidate"),)))
    assert "indisponible" in _summary(
        report(rows=(row("upload_candidate", executable=True),)))
    assert "indisponible" in _summary("PRIVATE path /home/PRIVATE")
