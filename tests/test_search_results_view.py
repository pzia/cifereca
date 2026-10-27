"""Headless result-view contracts using the real query/filter implementation."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.core.search import file_format, local_match_explanation
from cifereca.gui.query_model import QueryState, result_row
from cifereca.models import SearchResult


OWNER = Path(__file__).resolve().parents[1] / "src/cifereca/gui/search_results.py"


class Row:
    def __init__(self, key, title, author, meta, source, thumbnail, **options):
        self.key, self.title, self.source = key, title, source
        self.meta, self.options = meta, options


def _host():
    methods = [n for n in ast.parse(OWNER.read_text()).body if isinstance(n, ast.FunctionDef)]
    env = {"BookListItem": Row, "result_row": result_row, "file_format": file_format,
           "local_match_explanation": local_match_explanation}
    module = ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0), *methods], type_ignores=[]))
    exec(compile(module, str(OWNER), "exec"), env)
    host = NS(
        _search_mode="local", _search_local_objects={}, _remote_search_results=[],
        search_controls=NS(state=lambda: QueryState()),
        search_page=NS(show_rows=Mock(), count_label=NS(set_label=Mock()),
                       selection=NS(get_selected_item=lambda: None)),
        search_global_button=NS(set_sensitive=Mock()),
        service=NS(current_identity=NS(identity_id="alice"),
                   local_placements_for_sources=Mock(return_value={}),
                   provider_display_name=lambda p: p,
                   content_type_for=lambda item: "ebook",
                   can_direct_download=lambda item: False,
                   last_search_warnings=[]),
        _library_state_label=lambda item: "Local",
        _attach_row_facets=Mock(), _search_selected=Mock(),
        _open_placement=Mock(), download_direct_item=Mock(), toast=Mock(),
    )
    for method in methods:
        setattr(host, method.name, env[method.name].__get__(host))
    return host


def _book(provider, record="same"):
    return SearchResult(
        id=record, record_id=record, docbase="CAT", medium="digital", title="Dumas",
        author="Author", type=None, format="epub", publisher=None, published_at=None,
        description=None, thumbnail_url=None, friendly_url=None, scenario="EBOOKS",
        source_provider=provider, source_credential="credential",
    )


def test_local_filter_count_and_detail_reset_never_query_provider():
    host = _host()
    host.search_controls.state = lambda: QueryState(providers=("paris",))
    host._populate_local_search([
        {"key": "a", "title": "A", "provider_id": "paris"},
        {"key": "b", "title": "B", "provider_id": "gallica"},
    ])
    rows = host.search_page.show_rows.call_args.args[0]
    assert [r.key for r in rows] == ["a"]
    host.service.local_placements_for_sources.assert_not_called()
    host.search_page.count_label.set_label.assert_called_once_with("1 résultat · Connu")
    host._search_selected.assert_called_once_with(None)
    host.toast.assert_not_called()


def test_remote_ids_remain_distinct_and_local_placement_callback_is_pinned():
    host = _host()
    books = [_book("paris"), _book("gallica")]
    placement = {"placement_id": "owned-alice"}
    host.service.local_placements_for_sources.return_value = {
        ("paris", "credential", "same"): placement,
    }
    host._remote_search_results = books
    host._search_local_objects = {
        "local-owned": {"key": "local-owned", "title": "Dumas", "placement_id": "owned-alice"},
    }
    host._populate_search(books, announce=False)
    rows = host.search_page.show_rows.call_args.args[0]
    assert [r.key for r in rows] == ["paris:same", "gallica:same"]
    assert len(host.search_objects) == 2
    host.service.local_placements_for_sources.assert_called_once_with("alice")
    assert rows[0].options["action_label"] == "Ouvrir"
    host.service.current_identity = NS(identity_id="bob")
    rows[0].options["action_callback"](None)
    host._open_placement.assert_called_once_with(placement)
    host.toast.assert_not_called()


def test_progressive_warning_only_announced_once_and_real_selection_is_kept():
    host = _host()
    host.service.last_search_warnings = ["offline source"]
    host.search_page.selection.get_selected_item = lambda: Row("x", "X", "", "", "", None)
    host._populate_search([], announce=False)
    host.toast.assert_not_called()
    host._populate_search([], announce=True)
    host.toast.assert_called_once_with(
        "Résultats partiels · certaines sources sont indisponibles.", 6,
    )
    host._search_selected.assert_not_called()


def test_facade_passes_row_and_announcement_arguments_unchanged():
    main = OWNER.with_name("main_window.py")
    window = next(n for n in ast.parse(main.read_text()).body
                  if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    facade = next(n for n in window.body if isinstance(n, ast.FunctionDef) and n.name == "_populate_search")
    adapter = Mock()
    env = {"search_results": adapter}
    module = ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0), facade], type_ignores=[]))
    exec(compile(module, str(main), "exec"), env)
    host, rows = object(), [object()]
    env[facade.name](host, rows, announce=False)
    adapter._populate_search.assert_called_once_with(host, rows, announce=False)


def test_provider_warning_with_signed_url_and_account_identifier_is_never_displayed():
    host = _host()
    host.service.last_search_warnings = [
        "Fournisseur: HTTP 403 https://example.test/private?token=SECRET-ABC "
        "/home/personne/secrets/alice@example.test"
    ]
    host.search_page.selection.get_selected_item = lambda: Row(
        "key", "Livre", "", "", "", None,
    )
    host._populate_search([], announce=True)
    message, timeout = host.toast.call_args.args
    assert timeout == 6
    assert message == "Résultats partiels · certaines sources sont indisponibles."
    for forbidden in ("SECRET-ABC", "alice@example.test", "example.test", "/home/"):
        assert forbidden not in message
    # The read-only service warnings remain available for the Core diagnostic
    # pipeline, and no extra provider request occurs from the view.
    assert host.service.last_search_warnings[0].startswith("Fournisseur: HTTP 403")
    host.service.local_placements_for_sources.assert_called_once_with("alice")


def test_partial_warning_does_not_toast_for_empty_or_progressive_batches():
    host = _host()
    host.service.last_search_warnings = []
    host._populate_search([], announce=True)
    host.toast.assert_not_called()
    host.service.last_search_warnings = ["private exception"]
    host._populate_search([], announce=False)
    host.toast.assert_not_called()


def test_stale_public_row_has_warning_but_no_download_or_placement_action():
    from dataclasses import replace

    host = _host()
    stale = replace(
        _book("public-provider"),
        downloadable=True,
        metadata={"search_provenance": "known_catalog_stale", "mirrored_at": 100},
    )
    host._remote_search_results = [stale]
    host.service.can_direct_download = Mock(return_value=True)
    host._populate_search([stale], announce=False)
    rows = host.search_page.show_rows.call_args.args[0]
    assert len(rows) == 1
    assert "Résultat connu · hors ligne" in rows[0].meta
    assert rows[0].options["action_label"] is None
    assert rows[0].options["action_callback"] is None
    host.service.can_direct_download.assert_not_called()
    host.download_direct_item.assert_not_called()


def test_stale_public_selection_never_starts_holdings_or_acquisition():
    from dataclasses import replace

    owner = OWNER.with_name("search_details.py")
    tree = ast.parse(owner.read_text())
    method = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_search_selected")
    namespace = {"result_row": result_row}
    module = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        method,
    ], type_ignores=[]))
    exec(compile(module, str(owner), "exec"), namespace)
    item = replace(_book("public-provider"), metadata={
        "search_provenance": "known_catalog_stale",
    })
    panel = NS(show_search=Mock(), empty=Mock())
    host = NS(
        _closed=False, _search_detail_request_generation=0,
        _cancel_cached_book_details=Mock(),
        _search_local_objects={},
        search_objects={"public-provider:stale": item},
        search_page=NS(detail=panel),
        _selected_search_id=None, _add_facets=Mock(),
        search_controls=NS(), runner=NS(submit=Mock()),
        _request_cached_book_details=Mock(),
        _add_local_book_actions=Mock(),
    )
    namespace["_search_selected"](host, NS(key="public-provider:stale"))
    panel.show_search.assert_called_once_with(item, loading=False)
    host.runner.submit.assert_not_called()
    host._request_cached_book_details.assert_not_called()


def test_stale_public_notice_exposes_local_index_date_not_live_status():
    from dataclasses import replace
    from datetime import datetime, timezone

    observed = int(datetime(2026, 10, 15, 12, 30, tzinfo=timezone.utc).timestamp())
    host = _host()
    item = replace(
        _book("public-provider"),
        downloadable=True,
        metadata={"search_provenance": "known_catalog_stale", "mirrored_at": observed},
    )
    host._remote_search_results = [item]
    host.service.can_direct_download = Mock(return_value=True)
    host._populate_search([item], announce=False)
    rows = host.search_page.show_rows.call_args.args[0]
    assert len(rows) == 1
    assert "Résultat connu · hors ligne" in rows[0].meta
    assert "Notice indexée localement le 15/10/2026 (UTC)" in rows[0].meta
    assert "disponible actuellement" not in rows[0].meta
    assert rows[0].options["action_label"] is None
    host.service.can_direct_download.assert_not_called()


def test_stale_public_notice_never_renders_untrusted_or_invalid_cache_dates():
    from dataclasses import replace

    for invalid in (None, True, False, -1, 0, "2026/SECRET", 10 ** 100):
        host = _host()
        item = replace(
            _book("public-provider"),
            metadata={
                "search_provenance": "known_catalog_stale",
                "mirrored_at": invalid,
            },
        )
        host._remote_search_results = [item]
        host._populate_search([item], announce=False)
        row = host.search_page.show_rows.call_args.args[0][0]
        assert "Résultat connu · hors ligne" in row.meta
        assert "Notice indexée localement" not in row.meta
        assert "SECRET" not in row.meta
        assert row.options["action_callback"] is None


def test_live_public_notice_does_not_claim_offline_or_render_cache_timestamp():
    from dataclasses import replace

    host = _host()
    item = replace(_book("public-provider"), metadata={
        "mirrored_at": 1792067400, "search_provenance": "live",
    })
    host._remote_search_results = [item]
    host._populate_search([item], announce=False)
    row = host.search_page.show_rows.call_args.args[0][0]
    assert "Résultat connu" not in row.meta
    assert "Notice indexée localement" not in row.meta


def test_stale_public_search_warns_partial_degraded_mode_without_provider_secrets():
    from dataclasses import replace

    host = _host()
    stale = replace(_book("public-provider"), metadata={
        "search_provenance": "known_catalog_stale", "mirrored_at": 1792067400,
    })
    host.service.last_search_warnings = [
        "Provider failed: https://private.invalid/?token=SECRET",
    ]
    host._remote_search_results = [stale]
    host._populate_search([stale], announce=True)
    host.toast.assert_called_once_with(
        "Résultats connus, recherche en ligne indisponible · résultats partiels.", 6,
    )
    assert "SECRET" not in repr(host.toast.call_args)
    # These results are only displayed; neither acquisition nor authorization
    # is ever initiated from the warning.
    assert host.download_direct_item.call_count == 0


def test_degraded_toast_requires_failure_warning_and_final_announcement():
    from dataclasses import replace

    host = _host()
    stale = replace(_book("public-provider"), metadata={
        "search_provenance": "known_catalog_stale", "mirrored_at": 1792067400,
    })
    host._remote_search_results = [stale]
    host._populate_search([stale], announce=True)
    host.toast.assert_not_called()
    host.service.last_search_warnings = ["source unavailable"]
    host._populate_search([stale], announce=False)
    host.toast.assert_not_called()
