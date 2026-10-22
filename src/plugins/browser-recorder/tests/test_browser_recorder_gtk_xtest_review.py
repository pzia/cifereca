"""P0 #2531 — physical XTEST keyboard through all 81 GTK review choices and ZIP.

All data is deliberately fictional. The collector is substituted with RAM-only
synthetic traffic, but every decision, pagination, ZIP preparation, confirmation
and export action is an X11/XTEST keyboard event on a real GTK widget.
The installed-ZIP + real Chromium network scenario is tested separately.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from time import sleep
import json
import shutil
import subprocess
from types import SimpleNamespace as NS
from zipfile import ZipFile

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_review_ui import PrivateReviewPanel
import browser_recorder_review_ui as ui

ORIGIN = "https://fictional-books.example.org"
SECRET = "FAKE_PRIVATE_CREDENTIAL_NEVER_EXPORT"


def flush():
    main = GLib.MainContext.default()
    for _ in range(200):
        if not main.pending():
            return
        main.iteration(False)
    pytest.fail("GTK main loop stayed busy")


def xtest(*args):
    p = subprocess.run(
        ["xdotool", *args], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=8, check=False,
    )
    assert p.returncode == 0, "XTEST key dispatch failed"
    return p.stdout.strip()


def press(widget, *key):
    assert widget.grab_focus()
    flush()
    # GTK4 ComboBox popovers and the surrounding ScrolledWindow may
    # schedule focus/scroll transitions that do not show as pending on
    # the main context immediately; let the native compositor settle.
    sleep(.04)
    flush()
    focused = widget.get_root().get_focus()
    assert focused is widget or focused.is_ancestor(widget)
    xtest("key", "--clearmodifiers", *key)
    flush()


def children(widget):
    out = []
    child = widget.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


class Future:
    def __init__(self, fn):
        self.fn = fn
        self.callback = None
        self.value = None
        self.finished = False

    def done(self):
        return self.finished

    def result(self):
        assert self.finished
        return self.value

    def add_done_callback(self, cb):
        self.callback = cb

    def finish(self):
        self.value = self.fn(None)
        self.finished = True
        self.callback(self)


class FakeTasks:
    def __init__(self):
        self.handle = None

    def submit(self, fn, **kwargs):
        assert kwargs["kind"] == "browser-recorder"
        self.handle = Future(fn)
        return self.handle


@pytest.mark.parametrize("width", (1280, 1366))
def test_xtest_81_choices_exact_zip_and_confirmed_export(
    monkeypatch, tmp_path: Path, width,
):
    assert shutil.which("xdotool"), "XTEST runner must install xdotool"
    Gtk.init()
    tasks = FakeTasks()
    trace = PrivateHttpTrace((ORIGIN,))
    for number in range(81):
        value = str(1000 + number)
        trace.exchanges.append(PrivateExchange(
            number + 1, "GET",
            url=ORIGIN + "/api/books?BookId=" + value,
            status=200, mime="application/json",
            response_headers={"content-type": "application/json"},
            response_body=json.dumps({
                "BookId": value, "access_token": SECRET,
                "title": "FICTIONAL_TITLE",
            }),
            response_body_state="captured",
        ))

    def synthetic(start, stop, **kwargs):
        assert start == ORIGIN + "/catalog"
        assert stop.is_set()
        assert kwargs["pause_control"] is not None
        return trace

    monkeypatch.setattr(ui, "record_http", synthetic)
    site = Gtk.Entry()
    site.set_text(ORIGIN + "/catalog")
    consent = Gtk.CheckButton(label="Consentement test")
    consent.set_active(True)
    panel = PrivateReviewPanel(
        Gtk, GLib, tasks, site, consent, lambda: False,
        export_directory=tmp_path / "exports",
    )
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    # Site and consent are now owned by the Capture tab of the panel.
    root.append(panel.widget)
    scroller = Gtk.ScrolledWindow()
    scroller.set_child(root)
    window = Gtk.Window()
    window.set_title(f"Cifereca XTEST ZIP fictional {width}")
    window.set_default_size(width, 850)
    window.set_child(scroller)
    window.present()
    flush()
    try:
        hits = xtest(
            "search", "--onlyvisible", "--name",
            f"Cifereca XTEST ZIP fictional {width}",
        ).splitlines()
        assert len(hits) == 1
        xtest("windowfocus", "--sync", hits[0])
        flush()

        # Preparation uses a fake in-memory capture; review/ZIP/export controls
        # below are exercised strictly through real XTEST keyboard events.
        panel.start.emit("clicked")
        panel.stop.emit("clicked")
        tasks.handle.finish()
        flush()
        assert panel._review is not None
        assert len(panel._review.local_rows()) == 81
        assert panel._review.pending() == 81
        assert not panel.plan_button.get_sensitive()

        decided = 0
        for page in range(4):
            rows = children(panel.candidates_box)
            assert len(rows) == (24 if page < 3 else 9)
            for index, row in enumerate(rows):
                combo = row.get_last_child()
                assert isinstance(combo, Gtk.ComboBoxText)
                assert combo.get_active_id() == "choose"
                # Physical 1/2/3 shortcut on the candidate's native GTK
                # row, independently of popover/combo GTK minor versions.
                # The widget still requires each identifier to be focused.
                shortcut = 2 if page == 0 and index == 0 else (
                    3 if page == 3 and index == 8 else 1
                )
                press(combo, str(shortcut))
                expected = {1: "anonymize", 2: "keep", 3: "exclude"}[shortcut]
                assert combo.get_active_id() == expected
                decided += 1
            assert panel._review.pending() == 81 - decided
            assert not panel.export_button.get_sensitive()
            assert panel.candidate_page_status.get_label() == (
                f"Identifiants {page * 24 + 1}–"
                f"{min(81, (page + 1) * 24)} sur 81"
                f" · {81 - decided} à décider"
            )
            if page < 3:
                assert panel.candidate_next.get_sensitive()
                press(panel.candidate_next, "space")
                assert panel._candidate_page == page + 1
                flush()

        assert decided == 81
        assert panel._review.pending() == 0
        assert panel.plan_button.get_sensitive()
        exports = tmp_path / "exports"
        assert not exports.exists()
        press(panel.plan_button, "space")
        preview = panel._bundle_preview
        assert preview is not None and len(preview["members"]) >= 3
        assert not panel.export_button.get_sensitive()
        assert not exports.exists()

        # Native focus/Down switches the exact ZIP member preview.
        combo = panel.bundle_member
        before = combo.get_active_id()
        press(combo, "Down")
        press(combo, "Return")
        after = combo.get_active_id()
        assert after is not None and after != before
        # GtkComboBoxText may keep its X11 popup up after Return. Close it
        # before moving to the export checkbox, otherwise Space can be
        # consumed by the popover instead of toggling explicit consent.
        press(combo, "Escape")
        member = next(m for m in preview["members"] if m["name"] == after)
        buff = panel.bundle_text.get_buffer()
        assert buff.get_text(buff.get_start_iter(), buff.get_end_iter(), True) == member["text"]

        # Space on a disabled export button cannot cause an unreviewed write.
        assert not exports.exists()
        press(panel.export_confirm, "space")
        assert panel.export_confirm.get_active()
        assert panel.export_button.get_sensitive()
        def choose(done):
            exports.mkdir(mode=0o700, exist_ok=True)
            done(exports / "xtest-reviewed.zip")
        panel._prompt_export_file = choose
        press(panel.export_button, "space")
        paths = list(exports.glob("*.zip"))
        assert len(paths) == 1
        assert paths[0].stat().st_mode & 0o777 == 0o600
        with ZipFile(paths[0]) as archive:
            assert archive.testzip() is None
            assert sorted(archive.namelist()) == sorted(
                member["name"] for member in preview["members"]
            )
            plain = []
            for member in preview["members"]:
                data = archive.read(member["name"])
                assert data == member["text"].encode("utf-8")
                assert sha256(data).hexdigest() == member["sha256"]
                plain.append(data)
            raw = b"\n".join(plain)
            assert SECRET.encode() not in raw
            assert b"1080" not in raw  # excluded the last exchange
            assert b"1000" in raw  # explicitly kept one identifier
            assert b"PSEUDO_" in raw
        assert not panel.export_button.get_sensitive()
    finally:
        panel.destroy()
        window.close()
        flush()
