"""P1 #2854: selected reader detail receives only short-lived USB evidence.

Uses the same headless GTK facades as the reader page and the explicit chooser
tests. No actual hardware, network, filesystem transfer or provider request.
"""
from __future__ import annotations

from dataclasses import replace
from time import time

import pytest

from cifereca.core.device_usb_dry_run import UsbDryRun
from cifereca.core.device_usb_preview import UsbPreviewRow
from cifereca.gui.reader_library_projection import (
    ReaderLibraryBook, ReaderLibrarySnapshot,
)
from cifereca.gui.reader_usb_decisions import project_reader_usb_decisions
from cifereca.gui import reader_page_controller
from cifereca.gui.reader_usb_preview_actions import request_reader_usb_preview
from tests.test_p1_2854_readers_master_detail_page import environment
from tests.test_p1_2854_reader_usb_preview_actions import (
    FAKE_GTK, rig,
)


def dry_run(book: ReaderLibraryBook, *, observed_at=None, reason=None):
    return UsbDryRun(
        identity_id="alice", device_id="reader-alice",
        status="scanned", scan_attested=True,
        observed_at=int(time()) if observed_at is None else observed_at,
        rows=(
            UsbPreviewRow(
                artifact_id=book.artifact_id, desired="keep",
                observed="missing", last_known="unknown",
                last_observed_at=None,
                action="upload_candidate",
                reason=reason or "revalidate_before_usb_write",
            ),
        ),
    )


def test_gtk_selected_book_shows_exact_ephemeral_result_without_execution(tmp_path):
    db, entries, host, local_actions = environment(tmp_path)
    book = entries[0]
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=book["artifact_id"], desired_state="keep",
    )
    host._render_readers()
    snapshot = host._reader_library_snapshot
    assert snapshot.identity_id == "alice"
    assert snapshot.device_id == "reader-alice"
    projected = project_reader_usb_decisions(
        report=dry_run(snapshot.books[0]), library=snapshot,
    )
    assert projected.status == "scanned"
    host._reader_usb_decision_details = projected
    host._reader_usb_detail_source_snapshot = snapshot
    groups = []
    host.readers_page.detail.add_group = lambda name, rows: groups.append((name, rows))
    host.readers_page.selected(host.readers_page.rows[0])
    assert len(groups) == 1
    assert groups[0][0] == "Dernier examen USB · lecture seule"
    text = repr(groups[0][1])
    assert "Envoi à examiner" in text
    assert "Absence observée" in text
    assert "nouvelle vérification" not in text or "autorisation" in text
    assert "Aucun transfert" not in text or "aucun transfert" in text
    assert local_actions[0]["identity_id"] == "alice"
    assert db.device_placement_get("reader-alice", book["artifact_id"])["desired_state"] == "keep"


def test_filter_refresh_invalidates_book_decision_and_prevents_stale_presence(tmp_path):
    db, entries, host, local_actions = environment(tmp_path)
    book = entries[0]
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=book["artifact_id"], desired_state="keep",
    )
    host._render_readers()
    snapshot = host._reader_library_snapshot
    details = project_reader_usb_decisions(
        report=dry_run(snapshot.books[0]), library=snapshot,
    )
    host._reader_usb_decision_details = details
    host._reader_usb_detail_source_snapshot = snapshot
    host._render_readers()
    assert host._reader_usb_decision_details is None
    assert host._reader_usb_detail_source_snapshot is None
    assert host._reader_library_snapshot is not snapshot
    groups = []
    host.readers_page.detail.add_group = lambda name, rows: groups.append((name, rows))
    host.readers_page.selected(host.readers_page.rows[0])
    assert groups == []


def test_profile_switch_revokes_old_decisions_without_leaking_alice_title(tmp_path):
    db, entries, host, local_actions = environment(tmp_path)
    book = entries[0]
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=book["artifact_id"], desired_state="keep",
    )
    host._render_readers()
    snap = host._reader_library_snapshot
    host._reader_usb_decision_details = project_reader_usb_decisions(
        report=dry_run(snap.books[0]), library=snap,
    )
    host._reader_usb_detail_source_snapshot = snap
    host.service.current_identity.identity_id = "bob"
    host._identity_context_generation += 1
    host._render_readers()
    assert host._reader_usb_decision_details is None
    assert host._reader_scope_identity == "bob"
    assert host._reader_library_snapshot.identity_id == "bob"
    groups = []
    host.readers_page.detail.add_group = lambda name, rows: groups.append((name, rows))
    host.readers_page.selected(host.readers_page.rows[0])
    assert groups == []
    assert "Private alice" not in repr(groups)


def test_callback_projects_current_selected_book_only(tmp_path, monkeypatch):
    db, host, root = rig(tmp_path)
    from cifereca.gui import reader_page_controller as page_controller
    data = ReaderLibraryBook(
        key="book-1", artifact_id="sha256:" + "f" * 64,
        placement_id="p1", title="Un livre", author="Autrice",
        thumbnail_url=None, desired_state="keep",
        current_presence="unknown", last_known="unknown",
        last_observed_at=None, label="À mettre",
    )
    snapshot = ReaderLibrarySnapshot(
        identity_id="alice", device_id="reader-alice",
        device_enabled=True, books=(data,),
        total_local=1, wanted_keep=1, wanted_remove=0,
        no_intention=0,
    )
    host._reader_library_snapshot = snapshot
    calls = []
    selected = type("Selected", (), {"key": "book-1"})()
    host.readers_page = type("Page", (), {
        "selection": type("Selection", (), {
            "get_selected_item": lambda self: selected
        })()
    })()
    monkeypatch.setattr(
        page_controller, "reader_selected",
        lambda window, row: calls.append((window, row.key)),
    )
    request_reader_usb_preview(host, object(), Gtk=FAKE_GTK)
    chooser = host._reader_usb_preview_chooser
    chooser.respond(str(tmp_path))
    assert calls == []
    assert len(host.runner.calls) == 1
    _label, _work, done, _error, _options = host.runner.calls[0]
    done(dry_run(data))
    assert len(calls) == 1
    assert calls[0][1] == "book-1"
    assert host._reader_usb_detail_source_snapshot is snapshot
    assert host._reader_usb_decision_details.books[0].book_key == "book-1"
    assert "Aperçu USB" in host._reader_status_label.text


@pytest.mark.parametrize("change", [
    "filter_refresh", "scope_replaced", "foreign_report", "stale_report",
])
def test_background_preview_does_not_attach_result_to_changed_library(
    tmp_path, monkeypatch, change,
):
    db, host, root = rig(tmp_path)
    book = ReaderLibraryBook(
        key="visible", artifact_id="sha256:" + "f" * 64,
        placement_id="p1", title="Visible", author="",
        thumbnail_url=None, desired_state="keep",
        current_presence="unknown", last_known="unknown",
        last_observed_at=None, label="À mettre",
    )
    snap = ReaderLibrarySnapshot(
        identity_id="alice", device_id="reader-alice",
        device_enabled=True, books=(book,),
        total_local=1, wanted_keep=1, wanted_remove=0, no_intention=0,
    )
    host._reader_library_snapshot = snap
    request_reader_usb_preview(host, object(), Gtk=FAKE_GTK)
    host._reader_usb_preview_chooser.respond(str(tmp_path))
    assert len(host.runner.calls) == 1
    _label, _work, done, _error, _opts = host.runner.calls[0]
    observed = dry_run(book)
    if change == "filter_refresh":
        host._reader_library_snapshot = replace(snap)
    elif change == "scope_replaced":
        host._reader_library_snapshot = replace(snap, device_id="reader-bob")
    elif change == "foreign_report":
        observed = replace(observed, identity_id="bob")
    else:
        observed = replace(observed, observed_at=int(time()) - 301)
    done(observed)
    assert getattr(host, "_reader_usb_decision_details", None) is None
    assert getattr(host, "_reader_usb_detail_source_snapshot", None) is None
    assert "PRIVATE" not in host._reader_status_label.text
