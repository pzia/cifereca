"""#5975: the *calling* Detail renderers may not veto a source brand.

These headless GTK fixtures execute both actual DetailPanel methods and the
shared offer action. A public catalogue description without a deliverable
offer never reaches the broker, while a complete hypothetical offer can reach
Core under any provider ID. No HTTP, purchases, ADEPT or mutations.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, call

import pytest

from cifereca.core.search import file_format
from cifereca.gui.detail_model import (
    book_detail_presentation, catalogue_availability_label,
    compact_availability_label, detail_language,
    search_detail_borrow_action_candidate, search_detail_support_label,
)
from cifereca.gui.query_model import result_row
from cifereca.plugin_api import document_links
from tests.test_core_p0_5975_public_catalog_offer_neutral_ui import listing
from tests.test_core_p1_1916_stale_detail_readonly import _show_search
from tests.test_core_ui_p1_1867_numilog_direct_download_scope import (
    _direct_download_method,
)

PANEL = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"


def _show_item_detail():
    tree = ast.parse(PANEL.read_text(encoding="utf-8"))
    klass = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    method = next(node for node in klass.body
                  if isinstance(node, ast.FunctionDef) and node.name == "show_item_detail")
    env = {
        "file_format": file_format,
        "book_detail_presentation": book_detail_presentation,
        "catalogue_availability_label": catalogue_availability_label,
        "compact_availability_label": compact_availability_label,
        "detail_language": detail_language,
        "search_detail_support_label": search_detail_support_label,
        "search_detail_borrow_action_candidate": search_detail_borrow_action_candidate,
        "result_row": result_row,
        "document_links": document_links,
        "_commercial_search_usage": lambda _item, _service, **_kw: "",
        "Adw": NS(PreferencesGroup=lambda: Mock(), ActionRow=lambda: Mock()),
    }
    module = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[
            ast.alias(name="annotations")], level=0), method,
    ], type_ignores=[]))
    exec(compile(module, str(PANEL), "exec"), env)
    return env["show_item_detail"]


def _panel(*, admitted=True):
    button = NS(connect=Mock())
    service = NS(
        provider_display_name=lambda provider: provider,
        availability_display_label=lambda _item, _detail: "Indisponible",
        can_direct_download=Mock(return_value=admitted),
        content_type_for=lambda _: "ebook",
    )
    window = NS(
        service=service,
        action_button=Mock(return_value=button),
        download_direct_item=Mock(),
        can_borrow_item=Mock(return_value=False),
    )
    panel = NS(
        window=window,
        show_presentation=Mock(),
        _decorate=Mock(),
        _append_content=Mock(),
        _actions=Mock(),
    )
    action = _direct_download_method()
    panel._direct_download_action = Mock(side_effect=lambda item: action(panel, item))
    return panel, button


def _offer(provider, *, exact: bool):
    item = listing(provider)
    if exact:
        return replace(
            item, downloadable=True,
            download_url="https://example.invalid/edition.epub",
        )
    return item


@pytest.mark.parametrize("provider", ("numilog-catalog", "other-commercial"))
@pytest.mark.parametrize("exact", (False, True))
@pytest.mark.parametrize("complete_detail", (False, True))
def test_both_real_detail_render_paths_admit_only_the_exact_neutral_offer(
    provider, exact, complete_detail,
):
    item = _offer(provider, exact=exact)
    panel, button = _panel(admitted=True)
    if complete_detail:
        detail = NS(
            title=item.title, author=item.author, thumbnail_url=None,
            type=None, year=None, holdings=[], description=None,
        )
        _show_item_detail()(panel, item, detail)
    else:
        _show_search()(panel, item)

    # The supplier never blocks entry into the shared decision.
    panel._direct_download_action.assert_called_once_with(item)
    if exact:
        expected = 2 if complete_detail else 1
        assert panel.window.service.can_direct_download.call_args_list == (
            [call(item)] * expected
        )
        panel.window.action_button.assert_called_once()
        panel._actions.assert_called_once_with([button])
        button.connect.assert_called_once()
        panel.window.download_direct_item.assert_not_called()
    else:
        # No private/public entitlement inferred from catalogue metadata.
        panel.window.service.can_direct_download.assert_not_called()
        panel.window.action_button.assert_not_called()
        panel._actions.assert_not_called()


@pytest.mark.parametrize("complete_detail", (False, True))
def test_core_negative_admission_keeps_exact_public_offer_inert(complete_detail):
    item = _offer("numilog-catalog", exact=True)
    panel, _button = _panel(admitted=False)
    if complete_detail:
        detail = NS(
            title=item.title, author=item.author, thumbnail_url=None,
            type=None, year=None, holdings=[], description=None,
        )
        _show_item_detail()(panel, item, detail)
        panel._direct_download_action.assert_not_called()
    else:
        _show_search()(panel, item)
        panel._direct_download_action.assert_called_once_with(item)
    panel.window.action_button.assert_not_called()
    panel._actions.assert_not_called()
    panel.window.download_direct_item.assert_not_called()


def test_detail_source_contains_no_second_supplier_download_veto():
    source = PANEL.read_text(encoding="utf-8")
    assert 'source_provider != "numilog-catalog"' not in source
    assert "public_catalogue_download_hint_allowed(item)" in source
    assert "self._direct_download_action(item)" in source
