"""P0 #2531: REAL GTK4 widgets on Xvfb, with fake private HTTP bytes.

No browser navigation, account, capture, screenshots or real HTTP traffic.
Complements real Chromium test_browser_recorder_chromium_e2e.py.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS
from zipfile import ZipFile

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # noqa: E402

from browser_recorder_http import PrivateHttpTrace, PrivateExchange
from browser_recorder_review_ui import PrivateReviewPanel

ORIGIN = "https://books.example.org"


class FakeHandle:
    def __init__(self, fn):
        self.fn = fn
        self._done = False
        self._callback = None
        self._value = None

    def done(self):
        return self._done

    def result(self):
        assert self._done
        return self._value

    def add_done_callback(self, callback):
        self._callback = callback

    def finish(self):
        assert self._callback is not None
        self._value = self.fn(None)
        self._done = True
        self._callback(self)


class FakeTasks:
    def __init__(self):
        self.handle = None

    def submit(self, fn, **kw):
        assert kw["kind"] == "browser-recorder"
        self.handle = FakeHandle(fn)
        return self.handle


def flush_glib():
    main = GLib.MainContext.default()
    for _ in range(40):
        if not main.pending():
            return
        main.iteration(False)
    pytest.fail("GTK callback delivery did not stabilize")


def test_real_gtk4_private_review_zip_export_keyboard_controls(
    monkeypatch, tmp_path,
):
    import browser_recorder_review_ui as view

    Gtk.init()
    tasks = FakeTasks()
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST",
        url=ORIGIN + "/api/books?ProductId=214&AccountId=acct-ABC7",
        request_body=json.dumps({
            "ProductId": 214, "AccountId": "acct-ABC7",
            "access_token": "FAKE_PRIVATE_TOKEN",
            "title": "PRIVATE_NONAPPROVED_BOOK",
        }),
        request_headers={"content-type": "application/json"},
    ))

    def fake_http(start, stop, **_kw):
        assert start.startswith(ORIGIN)
        assert stop.is_set()
        return trace

    monkeypatch.setattr(view, "record_http", fake_http)
    url = Gtk.Entry()
    url.set_text(ORIGIN + "/api/books")
    consent = Gtk.CheckButton()
    consent.set_active(True)
    panel = PrivateReviewPanel(
        Gtk, GLib, tasks, url, consent, lambda: False,
        export_directory=tmp_path / "exports",
    )
    window = Gtk.Window()
    window.set_default_size(1366, 850)
    window.set_child(panel.widget)
    window.present()
    flush_glib()
    try:
        assert panel.start.get_sensitive()
        assert not panel.export_button.get_sensitive()
        panel.start.emit("clicked")
        assert tasks.handle is not None
        panel.stop.emit("clicked")
        tasks.handle.finish()
        flush_glib()
        assert panel._review is not None
        rows = panel._review.local_rows()
        assert {r["value"] for r in rows} == {"214", "acct-ABC7"}
        child = panel.candidates_box.get_first_child()
        for row in rows:
            assert child is not None
            chooser = child.get_last_child()
            assert isinstance(chooser, Gtk.ComboBoxText)
            chooser.set_active_id(
                "anonymize" if row["value"] == "acct-ABC7" else "keep"
            )
            child = child.get_next_sibling()
        assert child is None
        flush_glib()
        assert panel.plan_button.get_sensitive()
        panel.plan_button.emit("clicked")
        flush_glib()
        assert panel._bundle_preview
        assert not list(tmp_path.iterdir())
        assert not panel.export_button.get_sensitive()
        member_name = panel.bundle_member.get_active_id()
        entry = next(x for x in panel._bundle_preview["members"]
                     if x["name"] == member_name)
        buf = panel.bundle_text.get_buffer()
        assert buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True) == entry["text"]
        panel.export_button.emit("clicked")  # UI also verifies on click
        assert not list(tmp_path.iterdir())
        panel.export_confirm.set_active(True)
        flush_glib()
        assert panel.export_button.get_sensitive()
        panel.export_button.emit("clicked")
        flush_glib()
        outputs = list((tmp_path / "exports").glob("*.zip"))
        assert len(outputs) == 1
        with ZipFile(outputs[0]) as archive:
            combined = "\n".join(
                archive.read(name).decode("utf-8") for name in archive.namelist()
            )
            assert "acct-ABC7" not in combined
            assert "FAKE_PRIVATE_TOKEN" not in combined
            assert "PRIVATE_NONAPPROVED_BOOK" not in combined
            assert "PSEUDO_" in combined
        assert not panel.export_button.get_sensitive()
        panel.clear.emit("clicked")
        flush_glib()
        assert not panel.has_private_data()
        assert not trace.exchanges
    finally:
        panel.destroy()
        window.close()
        flush_glib()


@pytest.mark.parametrize("width", (1280, 1366))
def test_real_gtk4_large_identifier_list_requires_each_decision_and_scrolls(
    monkeypatch, tmp_path, width,
):
    """80 real GTK identifier choices across pages; no unreviewed ZIP.

    Native widgets and allocations, synthetic traffic only; no XTEST input,
    network, real account, or user secrets. Interactive UX remains separate.
    """
    import browser_recorder_review_ui as view

    Gtk.init()
    tasks = FakeTasks()
    trace = PrivateHttpTrace((ORIGIN,))
    suffix = "?" + "&".join(f"BookId={1000 + n}" for n in range(80))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/api/books" + suffix, status=200,
    ))

    def fake_http(start, stop, **_kw):
        assert start.startswith(ORIGIN)
        assert stop.is_set()
        return trace

    monkeypatch.setattr(view, "record_http", fake_http)
    site = Gtk.Entry()
    site.set_text(ORIGIN + "/api/books")
    consent = Gtk.CheckButton()
    consent.set_active(True)
    panel = PrivateReviewPanel(
        Gtk, GLib, tasks, site, consent, lambda: False,
        export_directory=tmp_path / "exports",
    )
    window = Gtk.Window()
    window.set_default_size(width, 850)
    window.set_child(panel.widget)
    window.present()
    flush_glib()
    try:
        panel.start.emit("clicked")
        panel.stop.emit("clicked")
        tasks.handle.finish()
        flush_glib()
        assert len(panel._review.local_rows()) == 80
        assert panel._review.pending() == 80
        # GTK4 automatically wraps a non-Scrollable Gtk.Box in a Viewport.
        viewport = panel.candidates_box.get_parent()
        assert isinstance(viewport, Gtk.Viewport)
        scroller = viewport.get_parent()
        assert isinstance(scroller, Gtk.ScrolledWindow)
        assert scroller.get_child() is viewport
        assert viewport.get_child() is panel.candidates_box
        # Only 24 real GTK ComboBox widgets are instantiated at once;
        # page navigation rather than unbounded scrolling reaches all IDs.
        assert not panel.candidate_previous.get_sensitive()
        assert panel.candidate_next.get_sensitive()
        assert not panel.plan_button.get_sensitive()
        assert not panel.export_button.get_sensitive()
        assert not list(tmp_path.iterdir())

        total_reviewed = 0
        last_chooser = None
        for page in range(4):
            choices = []
            child = panel.candidates_box.get_first_child()
            while child is not None:
                chooser = child.get_last_child()
                assert isinstance(chooser, Gtk.ComboBoxText)
                choices.append(chooser)
                child = child.get_next_sibling()
            assert len(choices) == (24 if page < 3 else 8)
            assert panel.candidate_previous.get_sensitive() == (page > 0)
            assert panel.candidate_next.get_sensitive() == (page < 3)
            assert panel.candidate_page_status.get_label() == (
                f"Identifiants {page * 24 + 1}–"
                f"{min(80, (page + 1) * 24)} sur 80"
            )
            for chooser in choices:
                chooser.set_active_id("anonymize")
            total_reviewed += len(choices)
            assert panel._review.pending() == 80 - total_reviewed
            flush_glib()
            last_chooser = choices[-1]
            if page < 3:
                panel.candidate_next.emit("clicked")
                flush_glib()
        assert total_reviewed == 80
        assert panel._review.pending() == 0
        assert panel.plan_button.get_sensitive()
        panel.plan_button.emit("clicked")
        flush_glib()
        assert panel._bundle_preview is not None
        assert not panel.export_button.get_sensitive()
        assert not list(tmp_path.iterdir())

        # A changed decision on a later page revokes ZIP preview and consent;
        # navigation never implicitly resets or accepts other decisions.
        last_chooser.set_active_id("exclude")
        assert panel._bundle_plan is None
        assert not panel.export_confirm.get_active()
        assert not panel.export_button.get_sensitive()
        assert list(tmp_path.iterdir()) == []
        panel.candidate_previous.emit("clicked")
        flush_glib()
        assert panel._review.pending() == 0
        child = panel.candidates_box.get_first_child()
        assert child.get_last_child().get_active_id() == "anonymize"
        panel.clear.emit("clicked")
        flush_glib()
        assert trace.exchanges == []
    finally:
        panel.destroy()
        window.close()
        flush_glib()


@pytest.mark.parametrize("width", (1280, 1366, 1600))
def test_real_gtk4_primary_page_prefers_private_http_over_legacy_har(
    monkeypatch, tmp_path, width,
):
    """Actual plugin page GTK tree, not a panel constructed in isolation.

    Only synthetic local UI: zero browser navigations, captures or downloads.
    Keyboard focus/visibility is checked on real GTK widgets; XTEST physical
    keystrokes and installed-ZIP black-box use remain separate acceptance gates.
    """
    from browser_recorder_page import BrowserRecorderPage

    Gtk.init()
    tasks = FakeTasks()
    plugin = BrowserRecorderPage()
    plugin.activate(NS(
        data_directory=tmp_path,
        broker=lambda name: tasks if name == "tasks" else None,
    ))
    window = Gtk.Window()
    window.set_default_size(width, 850)
    root = plugin.build(None)
    window.set_child(root)
    window.present()
    flush_glib()
    try:
        assert tasks.handle is None  # building the page never starts Chromium
        children = []
        item = root.get_first_child()
        while item is not None:
            children.append(item)
            item = item.get_next_sibling()
        assert len(children) == 6
        description, site, consent, workflow, private, advanced = children
        assert isinstance(site, Gtk.Entry)
        assert isinstance(consent, Gtk.CheckButton)
        assert private is plugin.private_panel.widget
        assert isinstance(advanced, Gtk.Expander)
        assert not advanced.get_expanded()
        assert "HAR" in advanced.get_label()
        assert "HTTP" in description.get_label()
        assert "ZIP" in workflow.get_label()
        assert plugin.private_panel.start.get_sensitive()
        assert not plugin.private_panel.export_button.get_sensitive()
        # Verify real GTK accepts focus on the consent and HTTP start
        # while the folded HAR controls are not user-visible.
        assert site.grab_focus()
        # Gtk.Entry delegates keyboard focus to its internal Gtk.Text child.
        focus = window.get_focus()
        assert focus is site or focus.is_ancestor(site)
        assert plugin.private_panel.start.grab_focus()
        assert window.get_focus() is plugin.private_panel.start
        legacy = advanced.get_child()
        assert isinstance(legacy, Gtk.Box)
        assert not legacy.get_mapped()
        advanced.set_expanded(True)
        flush_glib()
        assert advanced.get_expanded()
        assert legacy.get_mapped()
        assert tasks.handle is None
    finally:
        plugin.deactivate()
        window.close()
        flush_glib()


@pytest.mark.parametrize(("failure", "expected", "fragment"), [
    ("python_missing", "playwright_missing", "Installer les dépendances Python"),
    ("interrupted", "capture_interrupted", "Navigateur fermé"),
    ("unexpected", "capture_failed", "Vérifier Chromium"),
])
def test_gtk_capture_failure_is_attested_and_privacy_safe(
    monkeypatch, tmp_path, failure, expected, fragment,
):
    """Explicit UI action -> scoped Core callback -> public Diagnostics.

    Runs with native GTK widgets and a synthetic task, not a browser/provider.
    Secrets in exceptions must never enter the public feed or GTK labels.
    """
    from browser_recorder import RecorderError
    import browser_recorder_review_ui as view
    from browser_recorder_page import BrowserRecorderPage
    from cifereca.core.browser_recorder_diagnostics import PLUGIN_ID
    from cifereca.core.diagnostic_event_feed import query_diagnostic_events
    from cifereca.diagnostics import Diagnostics
    from cifereca.plugins.manager import PluginManager

    secret = "FAKE_SECRET_NEVER_EXPORT"
    if failure == "python_missing":
        error = RecorderError("playwright_unavailable")
    elif failure == "interrupted":
        error = RecorderError("capture_interrupted")
    else:
        error = RuntimeError("https://example.invalid/?token=" + secret)

    def synthetic_failure(url, stop, **_kwargs):
        assert url == ORIGIN + "/catalog"
        raise error

    monkeypatch.setattr(view, "record_http", synthetic_failure)

    class FailureHandle:
        def __init__(self, work):
            self.work, self.callback = work, None
            self.completed, self.error = False, None

        def done(self):
            return self.completed

        def add_done_callback(self, callback):
            self.callback = callback

        def result(self):
            assert self.completed and self.error is not None
            raise self.error

        def finish(self):
            assert self.callback is not None
            try:
                self.work(None)
            except Exception as exc:
                self.error = exc
            self.completed = True
            self.callback(self)

    class Tasks:
        def __init__(self):
            self.handle = None
            self.submissions = 0

        def submit(self, work, **_kwargs):
            self.submissions += 1
            self.handle = FailureHandle(work)
            return self.handle

    Gtk.init()
    tasks = Tasks()
    diagnostics = Diagnostics()
    manager = NS(
        diagnostics=diagnostics,
        _records={
            PLUGIN_ID: NS(
                descriptor=NS(id=PLUGIN_ID),
                package=NS(manifest=NS(
                    package=NS(id="org.cifereca.technical.browser-recorder"),
                )),
            ),
        },
    )
    def report(message, status, detail=None, level="normal"):
        PluginManager._plugin_diag(
            manager, PLUGIN_ID, message, status, detail, level,
        )

    page = BrowserRecorderPage()
    page.activate(NS(
        data_directory=tmp_path,
        broker=lambda name: tasks if name == "tasks" else None,
        report_diagnostic=report,
    ))
    window = Gtk.Window()
    window.set_default_size(1280, 850)
    root = page.build(None)
    window.set_child(root)
    window.present()
    flush_glib()
    try:
        assert diagnostics.snapshot() == []
        site = root.get_first_child().get_next_sibling()
        consent = site.get_next_sibling()
        site.set_text(ORIGIN + "/catalog")
        assert not consent.get_active()
        page.private_panel.start.emit("clicked")
        flush_glib()
        assert tasks.submissions == 0
        assert diagnostics.snapshot() == []
        assert not list(tmp_path.iterdir())

        consent.set_active(True)
        page.private_panel.start.emit("clicked")
        assert tasks.submissions == 1
        assert diagnostics.snapshot() == []
        tasks.handle.finish()
        flush_glib()
        assert fragment in page.private_panel.status.get_label()
        assert secret not in page.private_panel.status.get_label()

        feed = query_diagnostic_events(
            diagnostics, log_path=tmp_path / "absent",
            source="core_events", module="PLUGIN",
        )
        assert feed["total"] == 1
        assert feed["events"][0]["detail"] == {
            "plugin_id": PLUGIN_ID, "operation": "http_capture",
            "code": expected,
        }
        assert secret not in repr(feed)
        assert "example.invalid" not in repr(feed)
        assert not list(tmp_path.iterdir())
        assert not page.private_panel.has_private_data()
    finally:
        page.deactivate()
        window.close()
        flush_glib()
