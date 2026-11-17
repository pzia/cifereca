"""P0 #5975: catalogue-only download hints are source-neutral and fail closed.

No HTTP, supplier login, acquisition, entitlement or GTK runtime needed.
Core retains the authoritative, execution-time download decision.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.offer_actions import public_catalogue_download_hint_allowed
from tests.test_search_results_view import _book, _host
from tests.test_core_ui_p1_1867_numilog_direct_download_scope import (
    _direct_download_method,
)


def listing(provider="synthetic-commercial"):
    return replace(
        _book(provider), source_credential="__public__",
        source_collection="public_catalog", source_account_id=None,
        owned=False, downloadable=False, download_url=None,
    )


@pytest.mark.parametrize("provider", ["numilog-catalog", "synthetic-commercial"])
@pytest.mark.parametrize("changes", [
    {},
    {"owned": True, "downloadable": True},
    {"downloadable": True},
    {"download_url": "https://example.invalid/book.epub"},
    {"owned": True, "downloadable": True,
     "download_url": "https://example.invalid/book.epub"},
    {"downloadable": True, "download_url": " https://example.invalid/book.epub"},
    {"source_account_id": "borrower", "downloadable": True,
     "download_url": "https://example.invalid/book.epub"},
    {"source_credential": "personal-secret", "downloadable": True,
     "download_url": "https://example.invalid/book.epub"},
])
def test_public_catalogue_without_exact_offer_never_consults_broker(provider, changes):
    item = replace(listing(provider), **changes)
    assert public_catalogue_download_hint_allowed(item) is False

    host = _host()
    host._remote_search_results = [item]
    host.service.can_direct_download = Mock(return_value=True)
    host._populate_search([item], announce=False)
    row = host.search_page.show_rows.call_args.args[0][0]
    assert row.options["action_label"] is None
    assert row.options["action_callback"] is None
    host.service.can_direct_download.assert_not_called()
    host.download_direct_item.assert_not_called()

    service = NS(can_direct_download=Mock(return_value=True))
    window = NS(service=service, action_button=Mock(),
                download_direct_item=Mock())
    panel = NS(window=window, _actions=Mock())
    _direct_download_method()(panel, item)
    service.can_direct_download.assert_not_called()
    window.action_button.assert_not_called()
    panel._actions.assert_not_called()


@pytest.mark.parametrize("provider", ["numilog-catalog", "synthetic-public"])
def test_exact_public_offer_delegates_to_core_without_brand_gate(provider):
    item = replace(
        listing(provider), downloadable=True,
        download_url="https://example.invalid/book.epub",
    )
    assert public_catalogue_download_hint_allowed(item) is True
    host = _host()
    host._remote_search_results = [item]
    host.service.can_direct_download = Mock(return_value=True)
    host._populate_search([item], announce=False)
    row = host.search_page.show_rows.call_args.args[0][0]
    assert row.options["action_label"] == "Télécharger"
    host.service.can_direct_download.assert_called_once_with(item)
    host.download_direct_item.assert_not_called()
    row.options["action_callback"](None)
    host.download_direct_item.assert_called_once_with(item, None)

    button = NS(connect=Mock())
    service = NS(
        can_direct_download=Mock(return_value=True),
        content_type_for=lambda _: "ebook",
    )
    window = NS(
        service=service, action_button=Mock(return_value=button),
        download_direct_item=Mock(),
    )
    panel = NS(window=window, _actions=Mock())
    _direct_download_method()(panel, item)
    service.can_direct_download.assert_called_once_with(item)
    window.action_button.assert_called_once()
    assert window.action_button.call_args.args[0] == "Télécharger EPUB"
    panel._actions.assert_called_once_with([button])


def test_other_public_delivery_contract_and_private_exact_purchases_remain_independent():
    ordinary_public = replace(
        _book("fictional-gutenberg"), source_collection="public",
        source_credential="__public__", owned=False, downloadable=True,
        download_url=None,
    )
    assert public_catalogue_download_hint_allowed(ordinary_public) is True
    host = _host()
    host._remote_search_results = [ordinary_public]
    host.service.can_direct_download = Mock(return_value=True)
    host._populate_search([ordinary_public], announce=False)
    row = host.search_page.show_rows.call_args.args[0][0]
    assert row.options["action_label"] == "Télécharger"
    host.service.can_direct_download.assert_called_once_with(ordinary_public)

    from cifereca.gui.offer_actions import private_notice_download_hint_allowed
    from tests.test_core_ui_p1_1867_numilog_direct_download_scope import (
        private_purchase, _proof, NumilogOfferFixture,
    )
    private = private_purchase()
    assert public_catalogue_download_hint_allowed(private) is True
    assert private_notice_download_hint_allowed(private) is False
    assert private_notice_download_hint_allowed(
        private, purchase_evidence=_proof(), identity_id="alice",
    ) is False
    assert private_notice_download_hint_allowed(
        private, service=NumilogOfferFixture(), identity_id="alice",
    ) is True


def test_no_supplier_dispatch_in_both_active_gtk_download_decisions():
    root = Path(__file__).resolve().parents[1]
    for name in ("search_results.py", "detail_panel.py"):
        src = (root / "src/cifereca/gui" / name).read_text(encoding="utf-8")
        assert 'item.source_provider == "numilog-catalog"' not in src
        assert "public_catalogue_download_hint_allowed(item)" in src
