"""P0 #1127: live Diagnostics Settings snapshots without GTK/provider side effects."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from cifereca.gui import settings_diagnostics_refresh_controller as controller

from cifereca.gui.settings_diagnostics_refresh_controller import refresh_settings_diagnostics


@pytest.fixture(autouse=True)
def _fake_canonical_broker(monkeypatch):
    monkeypatch.setattr(
        controller, "DiagnosticsBrokerImpl",
        lambda service: NS(snapshot=service._journal_snapshot),
    )


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
OWNER = ROOT / "settings_diagnostics_refresh_controller.py"
MAIN = ROOT / "main_window.py"


class Widget:
    def __init__(self, *, label=None, xalign=None):
        self.label = label
        self.xalign = xalign
        self.subtitle = None
        self.children = []
        self.rows = []
        self.classes = set()
        self.signals = {}
        self.lines = None

    def set_label(self, label):
        self.label = label

    def set_title(self, title):
        self.label = title

    def set_subtitle(self, subtitle):
        self.subtitle = subtitle

    def set_subtitle_lines(self, count):
        self.lines = count

    def add_css_class(self, name):
        self.classes.add(name)

    def set_valign(self, value):
        self.align = value

    def connect(self, signal, callback):
        self.signals[signal] = callback

    def click(self):
        self.signals["clicked"](self)

    def add_suffix(self, widget):
        self.children.append(widget)

    def add_row(self, widget):
        self.rows.append(widget)

    def append(self, widget):
        self.children.append(widget)


class Dropdown:
    def __init__(self, selected=0):
        self.selected = selected

    def get_selected(self):
        return self.selected


class Buffer:
    def __init__(self):
        self.text = None

    def set_text(self, text):
        self.text = text


def plugin(name, *, attention, id=None, summary=None, technical=None):
    return {
        "plugin_id": id or name,
        "display_name": name,
        "state_label": "Erreur" if attention else "Prêt",
        "summary": summary or "Résumé",
        "attention": attention,
        "technical": technical or "",
    }


def make_context(*, ready=1, attention=0, rows=(), selected=0, paris_url=True):
    calls = []
    report = Buffer()
    diag_text = NS(get_buffer=lambda: report)
    box = Widget()
    summary = Widget()
    filter_widget = Dropdown(selected)
    lifetime = NS(alive=True)
    config = NS(get=lambda key: "https://config.example.invalid")
    if paris_url:
        config.paris_base_url = lambda: "https://paris.example.invalid"
    host = NS(
        _closed=False,
        plugin_manager=object(),
        diagnostics=NS(text=lambda *_: (_ for _ in ()).throw(
            AssertionError("legacy Diagnostics.text() must not be used")
        )),
        config=config,
        covers=NS(stats=lambda: {"covers": 2}),
        history_cover_resolver=NS(stats=lambda: {"history": 3}),
        ui_perf=NS(snapshot=lambda: {"perf": 4}),
        service=NS(provider_diagnostic_state=lambda: {"source": "ready"}),
    )
    host.service._journal_snapshot = lambda: (
        calls.append(("journal", host.service)),
        ({
            "source": "journal", "event_id": "physical-1",
            "module": "TEST", "operation": "refresh",
            "detail": {"login": "reader@example.invalid", "path": "/tmp/book.epub"},
        },),
    )[1]
    args = {
        "page_lifetime": lifetime,
        "diag_summary": summary,
        "diag_filter": filter_widget,
        "diag_issues_box": box,
        "diag_text": diag_text,
        "open_diagnostic_plugin": lambda button, pid: calls.append(("open", pid)),
        "Gtk": NS(Label=Widget, Button=Widget, Align=NS(CENTER="center")),
        "Adw": NS(ExpanderRow=Widget, ActionRow=Widget),
        "clear_box": lambda container: container.children.clear(),
        "diagnostic_plugin_rows": lambda data, *, attention_only: (
            calls.append(("project", attention_only)), list(rows)
        )[1],
        "system_status": lambda manager: (
            calls.append(("status", manager)), {
                "ready": ready, "attention": attention,
            }
        )[1],
        "version": "test-version",
    }
    return host, args, calls, report


def test_headless_controller_and_settings_builder_delegation():
    source = OWNER.read_text(encoding="utf-8")
    owner = ast.parse(source)
    assert "import gi" not in source and "from .main_window" not in source
    assert any(isinstance(n, ast.FunctionDef)
               and n.name == "refresh_settings_diagnostics" for n in owner.body)
    main = ast.parse(MAIN.with_name("settings_diagnostics_actions.py").read_text(encoding="utf-8"))
    builder = next(x for x in main.body if isinstance(x, ast.FunctionDef) and x.name == "bind_settings_diagnostics")
    nested = next(x for x in ast.walk(builder) if isinstance(x, ast.FunctionDef)
                  and x.name == "refresh_text")
    body = ast.unparse(nested)
    assert "settings_diagnostics_refresh_controller.refresh_settings_diagnostics" in body
    assert "page_lifetime=page_lifetime" in body
    assert "open_diagnostic_plugin=open_diagnostic_plugin" in body


def test_disposed_settings_page_prevents_status_and_render():
    host, args, calls, report = make_context()
    args["page_lifetime"].alive = False
    refresh_settings_diagnostics(host, **args)
    assert calls == []
    assert report.text is None


def test_closed_window_prevents_status_and_render_even_with_live_lifetime():
    host, args, calls, report = make_context()
    host._closed = True
    refresh_settings_diagnostics(host, **args)
    assert calls == []
    assert args["diag_summary"].label is None


def test_empty_attention_filter_reports_clear_state_without_leaking_sources():
    host, args, calls, report = make_context(ready=5)
    refresh_settings_diagnostics(host, **args)
    assert "5 prêt(s) · 0 à examiner." in args["diag_summary"].label
    assert "Aucune anomalie bloquante détectée." in args["diag_summary"].label
    assert len(args["diag_issues_box"].children) == 1
    empty = args["diag_issues_box"].children[0]
    assert empty.label == "Aucune anomalie à examiner."
    assert "success" in empty.classes
    assert "Source: journal canonique local" in report.text
    assert "reader@example.invalid" in report.text
    assert [x[0] for x in calls] == ["status", "project", "journal"]


def test_attention_priority_summary_and_filter_selected():
    host, args, calls, _ = make_context(
        ready=2, attention=3, rows=(plugin("Source Kobo", attention=True),),
    )
    refresh_settings_diagnostics(host, **args)
    assert "2 prêt(s) · 3 à examiner." in args["diag_summary"].label
    assert "Les anomalies sont affichées en premier." in args["diag_summary"].label
    assert ("project", True) in calls
    item = args["diag_issues_box"].children[0]
    assert item.label == "Source Kobo · Erreur"
    assert item.subtitle == "Résumé"
    assert "warning" in item.classes


def test_all_filter_preserves_clean_plugins_without_warning():
    host, args, calls, _ = make_context(
        selected=1, rows=(plugin("Source", attention=False),),
    )
    refresh_settings_diagnostics(host, **args)
    assert ("project", False) in calls
    item = args["diag_issues_box"].children[0]
    assert item.label == "Source · Prêt"
    assert "warning" not in item.classes


def test_action_button_targets_each_exact_plugin_without_late_binding():
    host, args, calls, _ = make_context(rows=(
        plugin("Paris", attention=True, id="provider-paris"),
        plugin("Kobo", attention=True, id="provider-kobo"),
    ))
    refresh_settings_diagnostics(host, **args)
    items = args["diag_issues_box"].children
    assert len(items) == 2
    buttons = [item.rows[0].children[0] for item in items]
    assert [button.label for button in buttons] == ["Ouvrir Plugins"] * 2
    assert all(button.align == "center" for button in buttons)
    buttons[0].click()
    buttons[1].click()
    assert [call for call in calls if call[0] == "open"] == [
        ("open", "provider-paris"), ("open", "provider-kobo"),
    ]


def test_technical_detail_has_explicit_fallback_and_unbounded_subtitle():
    host, args, calls, _ = make_context(rows=(
        plugin("Source", attention=True, technical=""),
        plugin("Autre", attention=False, technical="Détails exacts"),
    ))
    refresh_settings_diagnostics(host, **args)
    items = args["diag_issues_box"].children
    assert items[0].rows[1].subtitle == "Aucun détail technique"
    assert items[1].rows[1].subtitle == "Détails exacts"
    assert all(item.rows[1].lines == 0 for item in items)
    assert all(item.rows[0].label == "Action" for item in items)


def test_refresh_replaces_old_plugin_widgets_instead_of_duplication():
    host, args, calls, _ = make_context(rows=(
        plugin("Source", attention=True),),
    )
    refresh_settings_diagnostics(host, **args)
    old = args["diag_issues_box"].children[0]
    refresh_settings_diagnostics(host, **args)
    assert len(args["diag_issues_box"].children) == 1
    assert args["diag_issues_box"].children[0] is not old
    assert [event[0] for event in calls].count("status") == 2


def test_report_preserves_existing_provenance_performance_and_provider_state():
    host, args, calls, report = make_context()
    refresh_settings_diagnostics(host, **args)
    assert report.text.startswith("cifereca vtest-version")
    for snippet in (
        'transport: "httpx local direct"',
        'base_url: "https://paris.example.invalid"',
        'ui: "GTK4/libadwaita"',
        'cover_pipeline: {"covers": 2}',
        'history_cover_search: {"history": 3}',
        'ui_performance: {"perf": 4}',
        'providers: {"source": "ready"}',
        '"event_id": "physical-1"',
        '"login": "reader@example.invalid"',
        '"path": "/tmp/book.epub"',
    ):
        assert snippet in report.text
    assert [event[0] for event in calls] == ["status", "project", "journal"]


def test_report_falls_back_to_config_base_url_without_paris_helper():
    host, args, calls, report = make_context(paris_url=False)
    refresh_settings_diagnostics(host, **args)
    assert 'base_url: "https://config.example.invalid"' in report.text


def test_no_plugin_activation_or_background_job_in_refresh_path():
    host, args, calls, _ = make_context()
    host.run_kobo_backup = lambda **_: (_ for _ in ()).throw(AssertionError("backup"))
    host.reconnect_sources = lambda: (_ for _ in ()).throw(AssertionError("reconnect"))
    host.runner = NS(submit=lambda *_: (_ for _ in ()).throw(AssertionError("task")))
    refresh_settings_diagnostics(host, **args)
    assert [event[0] for event in calls] == ["status", "project", "journal"]


def test_settings_preview_reads_single_physical_journal_without_memory(
    tmp_path, monkeypatch,
):
    from cifereca.brokers import DiagnosticsBrokerImpl as RealBroker

    journal = tmp_path / "cifereca.log"
    journal.write_text(
        '2026-10-25 13:20:00,123 INFO cifereca [MainThread] DIAG_JSON '
        '{"schema_version":1,"category":"LOAN","operation":"import_acsm",'
        '"status":"error","message":"import failed","detail":'
        '{"login":"alice@example.org","password":"VISIBLE",'
        '"token":"KEEP","path":"/home/alice/book.epub"}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(controller, "DiagnosticsBrokerImpl", RealBroker)
    monkeypatch.setattr("cifereca.paths.log_file", lambda: journal)
    host, args, _calls, report = make_context()
    refresh_settings_diagnostics(host, **args)
    assert '"operation": "import_acsm"' in report.text
    for value in (
        "alice@example.org", "VISIBLE", "KEEP", "/home/alice/book.epub",
    ):
        assert value in report.text
    assert '"event_id":' in report.text


def test_settings_preview_reports_journal_failure_without_memory_fallback(monkeypatch):
    class FailedBroker:
        def __init__(self, service):
            self.service = service

        def snapshot(self):
            raise OSError("private path should not be displayed")

    monkeypatch.setattr(controller, "DiagnosticsBrokerImpl", FailedBroker)
    host, args, _calls, report = make_context()
    refresh_settings_diagnostics(host, **args)
    assert "Lecture du journal indisponible : OSError" in report.text
    assert "private path should not be displayed" not in report.text
