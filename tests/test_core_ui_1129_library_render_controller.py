"""P0 #1129 — exact Library/Recent rendering delegation without GTK."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui import library_render_controller as render


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "library_render_controller.py"


class FakeControls:
    def __init__(self, events, name="library"):
        self.events = events
        self.name = name

    def state(self):
        return self

    def filter(self, entries):
        self.events.append(("filter", self.name, len(entries)))
        return entries[:2]


def _library(*, count=3, pending=False, selected=None):
    events = []
    data = [{"key": "book-" + str(i)} for i in range(count)]
    row_objs = [object(), object()]
    page = NS(
        show_rows=lambda rows, **opts: events.append(
            ("show_rows", tuple(rows), opts["total_count"])
        ),
        selection=NS(get_selected_item=lambda: (
            events.append(("selection",)) or selected
        )),
    )
    window = NS(
        service=NS(current_identity=NS(identity_id="alice"),
                   provider_display_name=lambda provider: provider),
        library_page=page,
        library_controls=FakeControls(events),
        _library_entries_cache=[{"key": "old"}],
        _library_availability_label=lambda item: "",
        _kobo_backup_status_label=lambda item: "",
        _attach_row_facets=lambda rows, objects, controls: events.append(
            ("facets", rows, objects, controls)
        ),
        _select_pending_library_item=lambda: (
            events.append(("restore_pending",)) or pending
        ),
        _library_selected=lambda row: events.append(("detail", row)),
        ui_perf=NS(record=lambda name, elapsed: events.append(
            ("perf", name, elapsed)
        )),
    )

    def project(items, **kwargs):
        entries = list(items)
        events.append(("projection", len(entries)))
        assert kwargs["row_factory"] is object
        assert kwargs["format_date"] is str
        assert kwargs["provider_display_name"] == window.service.provider_display_name
        return row_objs[:len(entries)], {item["key"]: item for item in entries}

    counter = iter((10.0, 10.007))
    def call(entries=None):
        return render.render_library(
            window, entries,
            perf_counter=lambda: next(counter),
            project_library_rows=project,
            BookListItem=object, fr_date=str,
        )

    return window, data, row_objs, events, call


def test_headless_imports_and_exact_facade_apis():
    parsed = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(parsed)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(i == "import gi" or i.startswith("from gi ")
                   or "main_window" in i for i in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in main.body
               if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    for name, target, arg in (
        ("_render_library", "render_library", "entries"),
        ("_render_recent", "render_recent", "items"),
    ):
        func = methods[name]
        assert [x.arg for x in func.args.args] == ["self", arg]
        assert func.args.defaults[0].value is None
        assert ast.unparse(func.body[0].value.func) == "library_render_controller." + target
    source = ast.unparse(parsed)
    assert "time.sleep" not in source
    assert "runner.submit" not in source


def test_library_projects_only_filtered_rows_and_preserves_unfiltered_count():
    window, data, rows, events, call = _library(count=563)
    assert call(data) is None
    assert events[0] == ("filter", "library", 563)
    assert events[1] == ("projection", 2)
    assert events[2][0] == "facets"
    assert events[3] == ("show_rows", tuple(rows), 563)
    assert events[4] == ("restore_pending",)
    assert events[5] == ("selection",)
    assert events[6] == ("detail", None)
    assert events[7][0:2] == ("perf", "render_library")
    assert round(events[7][2]) == 7
    assert window._library_entries_cache == data
    assert window._library_entries_cache is not data
    assert len(window._library_objects) == 2


def test_restored_pending_selection_does_not_clear_detail():
    window, data, rows, events, call = _library(
        pending=True, selected=None,
    )
    call(data)
    assert not any(entry[0] == "selection" for entry in events)
    assert not any(entry[0] == "detail" for entry in events)
    assert events[3] == ("show_rows", tuple(rows), 3)
    assert events[4] == ("restore_pending",)


def test_library_render_without_page_is_inert():
    window = NS(_library_entries_cache=[{"key": "kept"}])
    called = []
    result = render.render_library(
        window, [{"key": "incoming"}],
        perf_counter=lambda: called.append("perf"),
        project_library_rows=lambda *_a, **_k: called.append("project"),
        BookListItem=object, fr_date=str,
    )
    assert result is None
    assert called == []
    assert window._library_entries_cache == [{"key": "kept"}]


def test_recent_projection_uses_search_facets_and_no_library_filters():
    events = []
    input_data = [{"key": "recent-a"}, {"key": "recent-b"}]
    controls = object()
    window = NS(
        recent_page=NS(show_rows=lambda rows: events.append(("show", rows))),
        _recent_entries_cache=[],
        search_controls=controls,
        _attach_row_facets=lambda rows, mapping, state: events.append(
            ("facets", rows, mapping, state)
        ),
        ui_perf=NS(record=lambda name, duration: events.append(
            ("perf", name, duration)
        )),
    )
    def project(items, **kwargs):
        events.append(("project", len(items), kwargs["row_factory"]))
        return ["first", "second"], {"recent-a": input_data[0]}

    time_points = iter((20.0, 20.004))
    render.render_recent(
        window, input_data, perf_counter=lambda: next(time_points),
        project_recent_rows=project, BookListItem=object,
    )
    assert events[0] == ("project", 2, object)
    assert events[1] == ("facets", ["first", "second"],
                         {"recent-a": input_data[0]}, controls)
    assert events[2] == ("show", ["first", "second"])
    assert events[3][0:2] == ("perf", "render_recent")
    assert round(events[3][2]) == 4
    assert window._recent_entries_cache == input_data
    assert window._recent_entries_cache is not input_data


def test_recent_render_without_page_does_not_scan_or_advance_clock():
    window = NS(_recent_entries_cache=[{"key": "kept"}])
    calls = []
    assert render.render_recent(
        window, [{"key": "new"}],
        perf_counter=lambda: calls.append("perf"),
        project_recent_rows=lambda *_a, **_k: calls.append("project"),
        BookListItem=object,
    ) is None
    assert calls == []
    assert window._recent_entries_cache == [{"key": "kept"}]
