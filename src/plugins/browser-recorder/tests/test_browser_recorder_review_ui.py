"""Headless GTK contract simulation; actual GTK/Chromium E2E remains #2531."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
import browser_recorder_review_ui as review_ui


ORIGIN = "https://books.example.org"


class Widget:
    def __init__(self, *args, **kwargs):
        self.children = []
        self.label = kwargs.get("label", "")
        self.callbacks = {}
        self.sensitive = True
        self.buffer = self
        self.text = ""

    def append(self, child):
        self.children.append(child)

    def set_child(self, child):
        self.children = [child]

    def get_first_child(self):
        return self.children[0] if self.children else None

    def remove(self, child):
        self.children.remove(child)

    def set_label(self, label):
        self.label = label

    def set_text(self, text):
        self.text = text

    def get_text(self):
        return self.text

    def get_buffer(self):
        return self.buffer

    def connect(self, event, callback):
        self.callbacks[event] = callback

    def set_sensitive(self, value):
        self.sensitive = value

    def __getattr__(self, key):
        if key.startswith("set_"):
            return lambda *_args: None
        raise AttributeError(key)


class Combo(Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.value = None
        self.options = {}

    def append(self, key, label):
        self.options[key] = label

    def set_active_id(self, key):
        self.value = key
        if "changed" in self.callbacks:
            self.callbacks["changed"](self)

    def get_active_id(self):
        return self.value

    def remove_all(self):
        self.options.clear()
        self.value = None


class Check(Widget):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.active = False

    def set_active(self, active):
        self.active = bool(active)
        if "toggled" in self.callbacks:
            self.callbacks["toggled"](self)

    def get_active(self):
        return self.active


class Gtk:
    Orientation = NS(VERTICAL=1, HORIZONTAL=2)
    SelectionMode = NS(NONE=0)
    WrapMode = NS(CHAR=1)
    Box = Widget
    FlowBox = Widget
    Label = Widget
    Button = Widget
    Separator = Widget
    ScrolledWindow = Widget
    TextView = Widget
    ComboBoxText = Combo
    CheckButton = Check
    Entry = Widget


class GLib:
    @staticmethod
    def idle_add(callback):
        return callback()


class FakeHandle:
    def __init__(self, work):
        self.work = work
        self._done = False
        self.callback = None
        self.value = None

    def done(self):
        return self._done

    def result(self):
        assert self._done
        return self.value

    def add_done_callback(self, fn):
        self.callback = fn

    def finish(self):
        self.value = self.work(None)
        self._done = True
        self.callback(self)


class FakeTasks:
    def __init__(self):
        self.handles = []

    def submit(self, work, **kwargs):
        assert kwargs["kind"] == "browser-recorder"
        handle = FakeHandle(work)
        self.handles.append(handle)
        return handle


def make_panel(monkeypatch, *, allowed=True, busy=False, export_directory=None):
    tasks = FakeTasks()
    site = NS(get_text=lambda: ORIGIN + "/catalog")
    consent = NS(get_active=lambda: allowed)
    raw = PrivateHttpTrace((ORIGIN,))
    raw.exchanges.append(PrivateExchange(
        1, "POST", url=ORIGIN + "/book?ProductId=214&accountId=ACC77",
        request_body=json.dumps({"ProductId": 214, "accountId": "ACC77",
                                 "access_token": "FAKE_AUTH_SECRET"}),
        request_headers={"content-type": "application/json"},
    ))

    def recorder(start, stop, *, pause_control=None, metadata_only_origins=()):
        assert start.startswith(ORIGIN) and stop.is_set()
        assert pause_control is not None
        assert type(metadata_only_origins) is tuple
        raw.third_party_scope = metadata_only_origins
        return raw

    monkeypatch.setattr(review_ui, "record_http", recorder)
    panel = review_ui.PrivateReviewPanel(
        Gtk, GLib, tasks, site, consent, legacy_running=lambda: busy,
        export_directory=export_directory,
    )
    return panel, tasks, raw


def test_private_http_review_requires_all_choices_and_never_exports(monkeypatch):
    panel, tasks, raw = make_panel(monkeypatch)
    panel.begin()
    assert len(tasks.handles) == 1 and panel.is_recording()
    assert not panel.clear.sensitive
    panel.stop_capture()
    tasks.handles[0].finish()
    assert panel.has_private_data() and not panel.is_recording()
    assert "2 identifiants" in panel.status.label
    panel.preview()
    assert "choix individuels" in panel.status.label
    combos = [row.children[-1] for row in panel.candidates_box.children]
    assert len(combos) == 2
    for row, combo in zip(panel._review.local_rows(), combos):
        combo.set_active_id("anonymize" if row["value"] == "ACC77" else "keep")
    panel.preview()
    local_preview = panel.preview_text.get_buffer().text
    assert "PSEUDO_" in local_preview
    assert "ProductId" in local_preview
    assert "ACC77" not in local_preview
    assert "FAKE_AUTH_SECRET" not in local_preview
    assert "approved_for_export" in local_preview
    assert '"approved_for_export": false' in local_preview
    panel.discard()
    assert not panel.has_private_data()
    assert not panel.preview_text.get_buffer().text
    assert raw.exchanges == []
    panel.destroy()


def test_inactive_consent_and_legacy_capture_block_http(monkeypatch):
    panel, tasks, raw = make_panel(monkeypatch, allowed=False)
    panel.begin()
    assert tasks.handles == []
    assert "Consentement" in panel.status.label
    panel.destroy()

    panel, tasks, raw = make_panel(monkeypatch, busy=True)
    panel.begin()
    assert tasks.handles == []
    panel.destroy()


def test_last_window_close_opens_same_review_but_never_exports(monkeypatch, tmp_path):
    panel, tasks, raw = make_panel(
        monkeypatch, export_directory=tmp_path / "exports",
    )
    panel.begin()
    panel.stop_capture()
    raw.end_reason = "browser_closed"
    tasks.handles[0].finish()
    assert "Capture terminée (navigateur fermé)" in panel.status.label
    assert "échanges" in panel.status.label
    assert panel._review is not None
    assert not panel.export_button.sensitive
    assert not list(tmp_path.iterdir())
    panel.destroy()


def test_empty_window_close_creates_no_review_or_archive(monkeypatch, tmp_path):
    panel, tasks, raw = make_panel(
        monkeypatch, export_directory=tmp_path / "exports",
    )
    raw.exchanges.clear()
    raw.end_reason = "browser_closed"
    panel.begin()
    panel.stop_capture()
    tasks.handles[0].finish()
    assert "aucune donnée recueillie" in panel.status.label
    assert panel._review is None
    assert not panel.has_private_data()
    assert not panel.export_button.sensitive
    assert not list(tmp_path.iterdir())
    panel.destroy()


def test_closing_during_capture_destroys_late_private_result(monkeypatch):
    panel, tasks, raw = make_panel(monkeypatch)
    panel.begin()
    handle = tasks.handles[0]
    panel.stop_capture()
    panel.destroy()
    handle.finish()
    assert raw.exchanges == []



def _capture_and_decide(panel, tasks):
    panel.begin()
    panel.stop_capture()
    tasks.handles[0].finish()
    for row, combo in zip(
        panel._review.local_rows(),
        [r.children[-1] for r in panel.candidates_box.children],
    ):
        combo.set_active_id("anonymize" if row["value"] == "ACC77" else "keep")


def test_exact_zip_preview_is_separate_from_explicit_export(tmp_path, monkeypatch):
    from hashlib import sha256
    from zipfile import ZipFile

    panel, tasks, raw = make_panel(
        monkeypatch, export_directory=tmp_path / "exports",
    )
    _capture_and_decide(panel, tasks)
    assert not panel.export_button.sensitive
    panel.prepare_zip()
    assert panel._bundle_plan is not None
    assert not list(tmp_path.iterdir())  # preparation never writes
    assert "SHA-256" in panel.bundle_summary.label
    assert not panel.export_button.sensitive
    assert panel.bundle_member.options
    chosen = panel.bundle_member.get_active_id()
    member = next(x for x in panel._bundle_preview["members"]
                  if x["name"] == chosen)
    assert panel.bundle_text.get_buffer().text == member["text"]
    assert sha256(member["text"].encode()).hexdigest() == member["sha256"]
    panel.export_zip()
    assert not list(tmp_path.iterdir())  # no explicit confirmation
    panel.export_confirm.set_active(True)
    assert panel.export_button.sensitive
    panel.export_zip()
    outputs = list((tmp_path / "exports").glob("*.zip"))
    assert len(outputs) == 1
    with ZipFile(outputs[0]) as zf:
        assert zf.read(chosen).decode("utf-8") == member["text"]
        all_plain = "".join(zf.read(x).decode("utf-8") for x in zf.namelist())
        assert "FAKE_AUTH_SECRET" not in all_plain
        assert "ACC77" not in all_plain
    assert not panel.export_button.sensitive
    assert panel._bundle_plan is None
    panel.destroy()
    assert raw.exchanges == []


def test_changed_identifier_decision_revokes_prepared_zip(tmp_path, monkeypatch):
    panel, tasks, raw = make_panel(
        monkeypatch, export_directory=tmp_path / "exports",
    )
    _capture_and_decide(panel, tasks)
    panel.prepare_zip()
    panel.export_confirm.set_active(True)
    combo = panel.candidates_box.children[0].children[-1]
    combo.set_active_id("exclude")
    assert panel._bundle_plan is None
    assert not panel.export_confirm.get_active()
    assert panel.bundle_text.get_buffer().text == ""
    panel.export_zip()
    assert list(tmp_path.iterdir()) == []
    panel.destroy()


def test_abandoned_zip_review_discards_unexported_bytes(tmp_path, monkeypatch):
    panel, tasks, raw = make_panel(
        monkeypatch, export_directory=tmp_path / "exports",
    )
    _capture_and_decide(panel, tasks)
    panel.prepare_zip()
    assert panel._bundle_plan is not None
    panel.destroy()
    assert panel._bundle_plan is None and raw.exchanges == []
    assert list(tmp_path.iterdir()) == []



def test_pause_resume_during_capture_is_explicit_and_resets_on_stop(monkeypatch):
    panel, tasks, raw = make_panel(monkeypatch)
    assert not panel.pause_button.sensitive
    panel.begin()
    control = panel._pause_control
    assert control is not None and panel.pause_button.sensitive
    assert control.state()[0] is False
    panel.pause_button.callbacks["clicked"](panel.pause_button)
    assert control.state()[0] is True
    assert panel.pause_button.label == "Reprendre HTTP privé"
    assert not tasks.handles[0].done()
    panel.pause_button.callbacks["clicked"](panel.pause_button)
    assert control.state()[0] is False
    panel.stop_capture()
    assert not panel.pause_button.sensitive
    tasks.handles[0].finish()
    assert panel._pause_control is None
    assert not panel.pause_button.sensitive
    panel.destroy()
    assert raw.exchanges == []



def test_third_party_scope_is_explicit_and_frozen_at_capture_start(monkeypatch):
    panel, tasks, raw = make_panel(monkeypatch)
    assert panel.third_party_origins.sensitive
    panel.third_party_origins.set_text(
        "https://identity.example.org, https://auth.example.org/"
    )
    panel.begin()
    assert len(tasks.handles) == 1
    assert not panel.third_party_origins.sensitive
    # Edits made while a browser is open must not broaden its approved scope.
    panel.third_party_origins.set_text("https://unapproved.example.org")
    panel.stop_capture()
    tasks.handles[0].finish()
    assert raw.third_party_scope == (
        "https://identity.example.org", "https://auth.example.org",
    )
    assert panel.third_party_origins.sensitive is False  # trace awaits review
    panel.discard()
    assert panel.third_party_origins.sensitive
    panel.destroy()


def test_invalid_third_party_scope_refuses_browser_without_leaking_origin(
    monkeypatch,
):
    from browser_recorder_review_ui import _metadata_only_origins
    from browser_recorder import RecorderError
    import pytest

    for raw in (
        "https://identity.example.org/path?code=PRIVATE_CODE",
        "https://identity.example.org?code=PRIVATE_CODE",
        "https://books.example.org",
        "http://localhost",
        "https://a.example.org,https://b.example.org,https://c.example.org,"
        "https://d.example.org,https://e.example.org",
        "https://identity.example.org,https://identity.example.org",
    ):
        with pytest.raises(RecorderError, match="invalid_origin"):
            _metadata_only_origins(raw, ORIGIN)
        panel, tasks, trace = make_panel(monkeypatch)
        panel.third_party_origins.set_text(raw)
        panel.begin()
        assert tasks.handles == []
        assert "Origines HTTPS invalides" in panel.status.label
        assert "PRIVATE_CODE" not in panel.status.label
        panel.destroy()
    assert _metadata_only_origins("", ORIGIN) == ()


def test_large_identifier_review_is_paginated_without_bulk_decisions(monkeypatch):
    """90 private IDs do not produce 90 GTK rows; choices survive navigation."""
    from browser_recorder_review import IdentifierReview

    panel, _tasks, _unused = make_panel(monkeypatch)
    trace = PrivateHttpTrace((ORIGIN,))
    for number in range(90):
        trace.exchanges.append(PrivateExchange(
            number + 1, "GET",
            url=ORIGIN + f"/api/books?AccountId=ACC{number:04d}",
            status=200,
        ))
    panel._trace = trace
    panel._review = IdentifierReview(trace)
    panel._show_candidates()
    assert len(panel._review.candidates()) == 90
    assert panel._review.pending() == 90
    assert len(panel.candidates_box.children) == 24
    assert "1–24 sur 90" in panel.candidate_page_status.label
    assert not panel.candidate_previous.sensitive
    assert panel.candidate_next.sensitive
    assert not panel.plan_button.sensitive

    first = panel.candidates_box.children[0].children[-1]
    first.set_active_id("anonymize")
    assert panel._review.pending() == 89

    panel.candidate_next.callbacks["clicked"](panel.candidate_next)
    assert "25–48 sur 90" in panel.candidate_page_status.label
    assert len(panel.candidates_box.children) == 24
    assert panel._review.pending() == 89
    panel.candidates_box.children[0].children[-1].set_active_id("keep")
    assert panel._review.pending() == 88

    panel.candidate_previous.callbacks["clicked"](panel.candidate_previous)
    assert panel.candidates_box.children[0].children[-1].get_active_id() == "anonymize"
    panel.candidate_next.callbacks["clicked"](panel.candidate_next)
    assert panel.candidates_box.children[0].children[-1].get_active_id() == "keep"

    panel.candidate_next.callbacks["clicked"](panel.candidate_next)
    panel.candidate_next.callbacks["clicked"](panel.candidate_next)
    assert "73–90 sur 90" in panel.candidate_page_status.label
    assert len(panel.candidates_box.children) == 18
    assert not panel.candidate_next.sensitive
    panel.prepare_zip()
    assert panel._bundle_plan is None
    assert "Toutes les décisions" in panel.status.label
    panel.discard()
    assert trace.exchanges == []
    assert not panel.candidate_next.sensitive
    panel.destroy()


def test_paginated_choices_all_require_explicit_review_before_zip(monkeypatch):
    """Pagination must neither auto-approve nor lose later-page decisions."""
    from browser_recorder_review import IdentifierReview

    panel, _tasks, _unused = make_panel(monkeypatch)
    trace = PrivateHttpTrace((ORIGIN,))
    for number in range(81):
        trace.exchanges.append(PrivateExchange(
            number + 1, "GET",
            url=ORIGIN + f"/api/books?BookId={1000 + number}",
            status=200,
        ))
    panel._trace = trace
    panel._review = IdentifierReview(trace)
    panel._show_candidates()
    assert panel._review.pending() == 81
    assert not panel.plan_button.sensitive
    for page in range(4):
        for row in panel.candidates_box.children:
            row.children[-1].set_active_id("anonymize")
        if page != 3:
            panel.change_candidate_page(1)
    assert panel._review.pending() == 0
    assert panel.plan_button.sensitive
    panel.change_candidate_page(-1)
    assert all(row.children[-1].get_active_id() == "anonymize"
               for row in panel.candidates_box.children)
    panel.prepare_zip()
    assert panel._bundle_plan is not None
    assert not panel.export_button.sensitive
    panel.destroy()
    assert trace.exchanges == []


def test_actionable_private_capture_failure_labels_never_echo_secrets(monkeypatch):
    from browser_recorder import RecorderError

    secret = "https://books.example.org/private?token=FAKE_SECRET_NEVER_EXPORT"
    cases = (
        (RecorderError("playwright_unavailable"), "Installer les dépendances Python"),
        (RuntimeError(secret), "Vérifier Chromium"),
        (RecorderError(secret), "Vérifier Chromium"),
        (RecorderError("capture_interrupted"), "Session Chromium interrompue"),
    )
    for error, expected in cases:
        panel, tasks, trace = make_panel(monkeypatch)
        try:
            panel.begin()
            assert len(tasks.handles) == 1
            handle = tasks.handles[0]
            handle._done = True

            def raise_error(exc=error):
                raise exc

            handle.result = raise_error
            handle.callback(handle)
            assert expected in panel.status.label
            assert "FAKE_SECRET_NEVER_EXPORT" not in panel.status.label
            assert "books.example.org" not in panel.status.label
            assert panel._trace is None
            assert panel._handle is None
            assert not panel.is_recording()
        finally:
            panel.destroy()
            trace.destroy()

    class Hostile:
        def __str__(self):
            raise AssertionError("private exception code stringified")
        def __repr__(self):
            raise AssertionError("private exception code repr rendered")

    assert review_ui._capture_failure_message(Hostile()).startswith(
        "Capture privée indisponible."
    )
