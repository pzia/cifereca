from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import BookRef, CachedBookDetail, CachedBookDetailContribution

MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
PLUGIN_RELOAD = MAIN.parent / "plugin_reload_controller.py"


def _source(name: str, *, class_name: str = "MainWindow") -> str:
    if name in {"_plugins_changed", "_subscribe_cached_book_details"}:
        module = ast.parse(PLUGIN_RELOAD.read_text(encoding="utf-8"))
        real_name = name[1:]
        method = next(n for n in module.body
                      if isinstance(n, ast.FunctionDef) and n.name == real_name)
        return ast.unparse(method)
    module = ast.parse(gui_source_text(MAIN))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    func = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.unparse(func)


def _host():
    module = ast.parse(gui_source_text(MAIN))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    names = {"_cancel_cached_book_details", "_request_cached_book_details",
             "_cached_book_cache_invalidated"}
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(methods) == len(names)
    compiled = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
              *methods], type_ignores=[]))
    from cifereca.plugin_api import book_ref_from_row
    ns = {
        "TaskPriority": NS(METADATA=1),
        "book_ref_from_row": book_ref_from_row,
        "collect_cached_book_details": lambda services, book, *, identity_id: (
            CachedBookDetailContribution("review", CachedBookDetail(book_id=book.book_id,
                                                                    identity_id=identity_id)),
        ),
    }
    exec(compile(compiled, str(MAIN), "exec"), ns)

    class Runner:
        def __init__(self):
            self.submitted = []

        def submit(self, title, work, done, err=None, **options):
            self.submitted.append(NS(title=title, work=work, done=done, err=err, options=options))

    def panel():
        values = []
        return NS(values=values, update_cached_book_details=lambda rows: values.append(tuple(rows)))

    host = type("Host", (), {name: ns[name] for name in names})()
    host.service = NS(current_identity=NS(identity_id="alice"))
    host._identity_context_generation = 1
    host._cached_book_request_generation = 0
    host._cached_book_context = None
    host._cached_book_services = [NS(plugin_id="review", service=object())]
    host._closed = False
    host.runner = Runner()
    host.search_page = NS(detail=panel())
    host.library_page = NS(detail=panel())
    return host


def test_cache_lookup_is_async_and_never_blocks_detail_render():
    host = _host()
    host._request_cached_book_details("library", "k1", {"item_id": "book-a"})
    assert host.library_page.detail.values == [()]
    assert len(host.runner.submitted) == 1
    task = host.runner.submitted[-1]
    assert task.options["kind"] == "detail-cache"
    task.done(task.work())
    assert host.library_page.detail.values[-1][0].detail.book_id == "book-a"


def test_switching_selected_book_drops_old_plugin_reply():
    host = _host()
    host._request_cached_book_details("library", "first", {"item_id": "a"})
    old = host.runner.submitted[-1]
    host._request_cached_book_details("library", "second", {"item_id": "b"})
    new = host.runner.submitted[-1]
    old.done(old.work())
    assert host.library_page.detail.values == [(), ()]
    new.done(new.work())
    assert host.library_page.detail.values[-1][0].detail.book_id == "b"


def test_identity_a_b_a_invalidates_same_book_cache_response():
    host = _host()
    host._request_cached_book_details("search", "same", {"item_id": "book-a"})
    stale = host.runner.submitted[-1]
    host.service.current_identity = NS(identity_id="bob")
    host._identity_context_generation += 1
    host.service.current_identity = NS(identity_id="alice")
    host._identity_context_generation += 1
    stale.done(stale.work())
    assert host.search_page.detail.values == [()]
    host._request_cached_book_details("search", "same", {"item_id": "book-a"})
    current = host.runner.submitted[-1]
    current.done(current.work())
    assert host.search_page.detail.values[-1][0].detail.identity_id == "alice"


def test_cache_invalidation_reloads_only_current_detail_not_list():
    host = _host()
    host._request_cached_book_details("search", "item", {"item_id": "book-a"})
    old = host.runner.submitted[-1]
    assert host._cached_book_cache_invalidated() is False
    new = host.runner.submitted[-1]
    assert len(host.runner.submitted) == 2
    old.done(old.work())
    assert host.search_page.detail.values == [(), ()]
    new.done(new.work())
    assert len(host.search_page.detail.values[-1]) == 1
    host._closed = True
    assert host._cached_book_cache_invalidated() is False
    assert len(host.runner.submitted) == 2


def test_no_plugin_or_no_identifier_skips_any_expensive_task():
    host = _host()
    host._cached_book_services = []
    host._request_cached_book_details("library", "item", {"item_id": "book-a"})
    host._cached_book_services = [NS(service=object())]
    host._request_cached_book_details("library", "missing", {"title": "No ID"})
    assert host.runner.submitted == []


def test_selection_and_remote_holdings_both_request_cached_detail():
    assert "_request_cached_book_details('library', row.key, item)" in _source("_library_selected")
    assert "_request_cached_book_details('search', row.key, item)" in _source("_search_selected")
    assert "_request_cached_book_details('search', item.id, result_row(item))" in _source("_search_selected")
    assert "_request_cached_book_details('search', item.id, result_row(item))" in _source("_apply_search_detail")
    assert "_request_cached_book_details('search', item.id, result_row(item))" in _source("_search_detail_error")


def test_new_selection_cancels_pending_and_identity_switch_invalidates_context():
    assert "self._cancel_cached_book_details()" in _source("_search_selected")
    assert "self._cancel_cached_book_details()" in _source("_library_selected")
    assert "self._cached_book_request_generation += 1" in _source("_activate_identity")
    assert "self._cached_book_context = None" in _source("_activate_identity")


def test_subscription_and_shutdown_do_not_leak_plugin_callbacks():
    assert "subscribe_cached_book_detail_changes(" in _source("_subscribe_cached_book_details")
    assert "idle_add(window._cached_book_cache_invalidated)" in _source("_subscribe_cached_book_details")
    assert "window._subscribe_cached_book_details()" in _source("_plugins_changed")
    assert "close_window_resources(self, source_remove=GLib.source_remove)" in _source("_on_close_request")
    assert "window._cached_book_unsubscribe()" in (MAIN.parent / "window_close.py").read_text(encoding="utf-8")


def test_rendering_keeps_provenance_freshness_and_validated_links():
    method = _source("update_cached_book_details", class_name="DetailPanel")
    assert "self._group(f'{section.title} · {plugin_id}', rows)" in method
    assert "'Cache ancien' if cached.stale else 'Cache local'" in method
    assert "safe_document_link_url(action.uri, relation='related')" in method
    assert "Gtk.LinkButton(uri=uri, label=str(action.title))" in method
    assert "self._cached_section_widgets.append(self.content.get_last_child())" in method
    assert "self._clear_actions()" in method
