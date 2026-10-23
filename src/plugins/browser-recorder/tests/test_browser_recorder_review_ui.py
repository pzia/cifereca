"""Headless GTK contract simulation; actual GTK/Chromium E2E remains #2531."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from types import SimpleNamespace as NS

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
import browser_recorder_review_ui as review_ui


ORIGIN = "https://books.example.org"


@pytest.mark.parametrize("code,fragment", [
    ("vault_unlock_failed", "bonne phrase"),
    ("vault_crypto_unavailable", "Installer les dépendances"),
    ("vault_unsafe_directory", "0700"),
    ("vault_unsafe_file", "actualisez"),
    ("vault_invalid", "incompatible"),
    ("private_zip_limit", "taille"),
    ("private_zip_destination", ".zip"),
    ("private_zip_exists", "autre nom"),
    ("private_zip_write_failed", "espace libre"),
])
def test_private_export_failures_are_actionable_and_retryable(monkeypatch, tmp_path, code, fragment):
    secret = "PRIVATE-PASSPHRASE-DO-NOT-PRINT"
    destroyed, outcomes = [], []
    panel = review_ui.PrivateReviewPanel(
        Gtk, GLib, FakeTasks(), NS(get_text=lambda: ORIGIN),
        NS(get_active=lambda: True), lambda: False,
        vault_directory=tmp_path / "vault",
        diagnostic=lambda *a, **kw: outcomes.append((a, kw)),
    )
    panel.session_list.append("synthetic", "synthetic")
    panel.session_list.set_active_id("synthetic")
    panel._vault_secret = bytearray(secret.encode())
    panel._prompt_private_zip_file = lambda accepted, cancelled: accepted(tmp_path / "private.zip")
    stored = NS(trace=NS(destroy=lambda: destroyed.append(True)))
    class Vault:
        def __init__(self, path):
            pass
        def open(self, sid, password):
            assert password == secret
            return stored
    monkeypatch.setattr(review_ui, "PrivateSessionVault", Vault)
    def fail(*a, **kw):
        raise review_ui.VaultError(code)
    monkeypatch.setattr(review_ui, "save_session_llm_zip", fail)
    try:
        panel.export_private_zip()
        assert fragment in panel.status.label
        assert destroyed == [True]
        assert secret not in panel.status.label + repr(outcomes)
        assert outcomes[-1][0] == ("private_zip_export", "private_zip_export_failed")
        assert panel._vault_secret.decode() == secret
        monkeypatch.setattr(review_ui, "save_session_llm_zip", lambda *a, **kw: 42)
        panel.export_private_zip()
        assert destroyed == [True, True]
        assert outcomes[-1][0] == ("private_zip_export", "private_zip_exported")
        assert panel.vault_passphrase.get_text() == ""
    finally:
        panel.destroy()


def test_private_export_unknown_error_is_never_rendered():
    class Hostile:
        def __str__(self):
            raise AssertionError("private argument rendered")
        __repr__ = __str__
    for error in (review_ui.VaultError(Hostile()),
                  review_ui.VaultError("https://private.invalid/?token=SECRET"),
                  RuntimeError("PRIVATE-PASSWORD")):
        message = review_ui._private_export_failure_message(error)
        assert "réessayez" in message
        assert "SECRET" not in message and "private.invalid" not in message


class Widget:
    def __init__(self, *args, **kwargs):
        self.children = []
        self.label = kwargs.get("label", "")
        self.callbacks = {}
        self.sensitive = True
        self.visible = True
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

    def set_visible(self, value):
        self.visible = value

    def set_sensitive(self, value):
        self.sensitive = value

    def __getattr__(self, key):
        if key.startswith("set_"):
            return lambda *_args: None
        raise AttributeError(key)


class Notebook(Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tab_titles = []
        self.current_page = 0

    def append_page(self, child, label):
        self.children.append(child)
        self.tab_titles.append(label.label)

    def set_current_page(self, page):
        self.current_page = page

    def get_current_page(self):
        return self.current_page


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
    PolicyType = NS(NEVER=0, AUTOMATIC=1)
    Widget = Widget
    Notebook = Notebook
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

    def recorder(start, stop, *, pause_control=None, metadata_only_origins=(),
                 download_destination=None, status=None, coverage_callback=None):
        assert start.startswith(ORIGIN) and stop.is_set()
        assert pause_control is not None
        assert type(metadata_only_origins) is tuple
        assert callable(download_destination)
        assert callable(status)
        raw.third_party_scope = metadata_only_origins
        return raw

    monkeypatch.setattr(review_ui, "record_http", recorder)
    panel = review_ui.PrivateReviewPanel(
        Gtk, GLib, tasks, site, consent, legacy_running=lambda: busy,
        export_directory=export_directory,
    )
    return panel, tasks, raw


def test_workspaces_separate_capture_sessions_review_without_export(monkeypatch):
    panel, tasks, raw = make_panel(monkeypatch)
    try:
        assert panel.tabs.tab_titles == [
            "Capture", "Sessions et exports", "Paramètres",
        ]
        assert panel.tabs.current_page == 0
        assert panel.configure_button in panel.capture_box.children
        assert any(panel.start in child.children for child in panel.capture_box.children)
        assert panel.export_button in panel.review_box.children
        assert panel.current_private_export_button is None
        assert any(panel.preview_button in child.children for child in panel.review_box.children)
        assert any(panel.clear in child.children for child in panel.review_box.children)
        assert not any(panel.preview_button in child.children for child in panel.capture_box.children)
        assert panel.status in panel.widget.children
        assert panel.export_button.sensitive is False
        panel.begin()
        handle = panel._handle
        generation = panel._generation
        assert panel.active_controls.visible
        assert panel.stop in panel.active_controls.children
        for page in (1, 2, 0):
            panel.tabs.set_current_page(page)
            assert panel._handle is handle and panel.is_recording()
            assert panel._generation == generation
            assert panel.stop.sensitive
        panel.stop_capture()
        tasks.handles[-1].finish()
        assert not panel.active_controls.visible
        assert panel.tabs.current_page == 1
        assert not panel.export_button.sensitive
    finally:
        panel.destroy()


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
        label = panel.candidates_box.children[combos.index(combo)].children[0].label
        assert "Confiance non évaluée" in label
        assert "suggestion heuristique" in label
        assert "Provenance locale : " + ", ".join(row["fields"]) in label
        assert row["value"] in label
        assert "FAKE_AUTH_SECRET" not in label
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


def test_member_name_filter_and_coverage_shortcut_do_not_change_consent(
    monkeypatch,
):
    panel, tasks, raw = make_panel(monkeypatch)
    try:
        _capture_and_decide(panel, tasks)
        panel.prepare_zip()
        names = list(panel.bundle_member.options)
        assert panel.bundle_member.get_active_id() == "manifest.json"
        assert "coverage-report.json" in names
        assert panel.coverage_button.sensitive
        assert not panel.export_button.sensitive
        panel.member_filter.set_text("coverage")
        panel._populate_zip_members()  # headless Entry has no changed signal
        assert list(panel.bundle_member.options) == ["coverage-report.json"]
        assert "1 membre(s) correspondant" in panel.bundle_progress.label
        assert panel.bundle_member.get_active_id() == "coverage-report.json"
        assert '"capture_state"' in panel.bundle_text.text
        assert not panel.export_confirm.get_active()
        panel.member_filter.set_text("NOT_FOUND")
        panel._populate_zip_members()
        assert list(panel.bundle_member.options) == []
        assert panel.bundle_text.text == ""
        panel._select_coverage_report()
        assert panel.member_filter.get_text() == ""
        assert panel.bundle_member.get_active_id() == "coverage-report.json"
        assert not panel.export_confirm.get_active()
        assert not panel.export_button.sensitive
    finally:
        panel.destroy()


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
    def choose(done):
        (tmp_path / "exports").mkdir(mode=0o700)
        done(tmp_path / "exports" / "fictional-reviewed.zip")
    panel._prompt_export_file = choose
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
    assert panel.candidate_next_pending.sensitive
    panel.go_to_next_pending()
    assert panel._pending_cursor == 72
    assert "Identifiant 73/90" in panel.status.label
    assert panel._review.pending() == 88
    panel.go_to_next_pending()
    assert panel._pending_cursor == 73
    assert "Identifiant 74/90" in panel.status.label
    assert panel._review.pending() == 88
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


def test_prepared_zip_shows_no_body_warning_in_real_summary_control(monkeypatch, tmp_path):
    panel, tasks, raw = make_panel(
        monkeypatch, export_directory=tmp_path / "exports",
    )
    raw.exchanges[0].request_body = None
    try:
        _capture_and_decide(panel, tasks)
        panel.prepare_zip()
        assert panel._bundle_plan is not None
        assert "ZIP sans aucun corps HTTP" in panel.bundle_summary.label
        assert "structures exportées : 0" in panel.bundle_summary.label
        assert "non retenus dans la trace : 2" in panel.bundle_summary.label
        assert not panel.export_button.sensitive
        assert not list(tmp_path.iterdir())
    finally:
        panel.destroy()


def test_saved_zip_shows_exact_local_path_and_opens_folder_only_when_clicked(
    tmp_path, monkeypatch,
):
    """No filesystem path reaches logs or Diagnostics; only the private GTK label."""
    import sys
    from types import ModuleType

    panel, tasks, raw = make_panel(
        monkeypatch, export_directory=tmp_path / "old-default",
    )
    try:
        _capture_and_decide(panel, tasks)
        assert not panel.open_export_folder_button.sensitive
        panel.open_export_folder()  # no prior successful save => no effect
        panel.prepare_zip()
        folder = tmp_path / "ZIP choisis"
        folder.mkdir(mode=0o700)
        destination = folder / "approved.zip"
        panel.export_confirm.set_active(True)
        panel._prompt_export_file = lambda callback: callback(destination)
        panel.export_zip()
        assert destination.exists()
        assert str(destination) in panel.status.label
        assert "aucun téléversement" in panel.status.label
        assert panel.open_export_folder_button.sensitive

        launched = []
        fake_gi = ModuleType("gi")
        fake_repository = ModuleType("gi.repository")
        fake_repository.Gio = NS(AppInfo=NS(
            launch_default_for_uri=lambda uri, context: launched.append(
                (uri, context)
            ),
        ))
        monkeypatch.setitem(sys.modules, "gi", fake_gi)
        monkeypatch.setitem(sys.modules, "gi.repository", fake_repository)
        assert launched == []  # Save-As never launches a file manager.
        panel.open_export_folder()
        assert launched == [(folder.as_uri(), None)]
    finally:
        panel.destroy()
        assert panel._last_export_path is None
        assert not panel.open_export_folder_button.sensitive
        assert raw.exchanges == []


def test_encrypted_capture_remains_exportable_when_identifier_review_fails(
    monkeypatch, tmp_path,
):
    """Private export is the explicit fallback when heuristic review fails."""
    import pytest
    from zipfile import ZipFile
    from browser_recorder_vault import PrivateSessionVault

    pytest.importorskip("cryptography")
    vault_path = tmp_path / "vault"
    secret = "FICTIONAL-STRONG-VAULT-PASSPHRASE"
    raw = PrivateHttpTrace((ORIGIN,))
    raw.exchanges.append(PrivateExchange(
        1, "POST", url=ORIGIN + "/api?AccountId=PRIVATE-BOUND-42",
        status=200, request_body='{"AccountId":"PRIVATE-BOUND-42"}',
        request_body_state="captured",
        request_headers={"content-type": "application/json"},
    ))

    def recorder(_start, stop, **kwargs):
        assert stop.is_set()
        writer = kwargs["vault_writer"]
        writer.save(raw, "completed", force=True)
        return raw

    def refuse_review(_trace):
        raise review_ui.RecorderError("review_limit")

    monkeypatch.setattr(review_ui, "record_http", recorder)
    monkeypatch.setattr(review_ui, "IdentifierReview", refuse_review)
    tasks = FakeTasks()
    panel = review_ui.PrivateReviewPanel(
        Gtk, GLib, tasks, NS(get_text=lambda: ORIGIN + "/catalog"),
        NS(get_active=lambda: True), legacy_running=lambda: False,
        vault_directory=vault_path,
    )
    destination = tmp_path / "private-llm.zip"
    try:
        panel.vault_consent.set_active(True)
        panel.vault_passphrase.set_text(secret)
        panel.begin()
        panel.stop_capture()
        tasks.handles[-1].finish()
        assert panel._review is None
        assert panel._trace is raw
        assert raw.vault_session_id
        assert panel.tabs.get_current_page() == 1
        assert panel.current_private_export_button.sensitive
        assert "Revue filtrée impossible" in panel.status.label
        assert (vault_path / (raw.vault_session_id + ".cbrv")).is_file()
        assert not destination.exists()

        panel._prompt_private_zip_file = lambda callback, cancel: callback(destination)
        panel.vault_passphrase.set_text(secret)
        panel.export_private_zip(current=True)
        assert destination.is_file()
        assert destination.stat().st_mode & 0o777 == 0o600
        with ZipFile(destination) as archive:
            assert "PRIVATE-BOUND-42" in archive.read("trace.json").decode()
        assert panel.vault_passphrase.get_text() == ""
        assert len(PrivateSessionVault(vault_path).list_ids()) == 1
    finally:
        panel.destroy()



def test_coverage_reports_empty_responses_and_near_limit_without_private_values(monkeypatch):
    panel, tasks, raw = make_panel(monkeypatch)
    try:
        panel._show_coverage({
            "exchanges": 237, "limit": 240, "text_responses": 0,
            "binary_skipped": 166, "compressed": 68, "out_of_scope": 631,
            "static_metadata_budget": 0,
            "json_responses_observed": 11, "json_responses_captured": 1,
            "html_responses_observed": 10, "html_responses_captured": 5,
            "write_requests_observed": 1, "write_requests_captured": 1,
            "response_size_unavailable": 10, "response_over_budget": 7,
        })
        assert "JSON 1/11" in panel.coverage_status.label
        assert "HTML 5/10" in panel.coverage_status.label
        assert "POST/PUT/PATCH 1/1" in panel.coverage_status.label
        assert "10 réponse(s) sans taille fiable" in panel.coverage_status.label
        assert "7 hors plafond" in panel.coverage_status.label
        assert "237/240" in panel.coverage_status.label
        assert "analyse des réponses impossible" in panel.coverage_status.label
        assert "presque atteinte" in panel.coverage_status.label
        panel.discard()
        assert panel.coverage_status.label == ""
    finally:
        panel.destroy()


def test_domain_inventory_requires_fresh_opt_in_and_clears_with_private_data(monkeypatch):
    panel, tasks, _raw = make_panel(monkeypatch)
    foreign = "https://diagnostic.example.org"
    trace = PrivateHttpTrace((ORIGIN,), diagnose_origins=True)
    trace.request(NS(url=foreign + "/private?token=PRIVATE-QUERY"))
    trace.exchanges.append(PrivateExchange(1, "GET", url=ORIGIN + "/catalog", status=200))
    seen = []
    def recorder(_start, stop, **kwargs):
        assert stop.is_set()
        seen.append(kwargs.get("diagnose_origins"))
        return trace
    monkeypatch.setattr(review_ui, "record_http", recorder)
    try:
        assert not panel.diagnose_origins.get_active()
        panel.diagnose_origins.set_active(True)
        panel.begin()
        assert not panel.diagnose_origins.get_active()  # next capture requires fresh consent
        assert not panel.diagnose_origins.sensitive
        panel.stop_capture()
        tasks.handles[-1].finish()
        assert seen == [True]
        assert foreign in panel.excluded_domains.label
        assert "PRIVATE-QUERY" not in panel.excluded_domains.label
        panel.discard()
        assert panel.excluded_domains.label == ""
        assert trace.origin_diagnostics() == ({}, 0)
    finally:
        panel.destroy()
