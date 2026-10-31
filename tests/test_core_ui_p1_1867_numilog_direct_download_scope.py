"""#1867: only private Numilog inventory can show a direct acquisition affordance.

Pure synthetic sources: no HTTP, ACSM, ADEPT, login, downloaded data or mutation.
The locked acquisition executor independently revalidates all rights.
"""
from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.search import file_format
from cifereca.services import LibraryService
from cifereca.gui.query_model import result_row
from tests.test_search_results_view import _book, _host


CREDENTIAL = "personal-numilog-A"


def private_purchase():
    """Exact normalized Numilog lid/variant under the configured credential."""
    scope = hashlib.sha256(CREDENTIAL.encode()).hexdigest()[:20]
    record_id = f"numilog:{scope}:42:{'a' * 16}"
    return replace(
        _book("numilog", record=record_id),
        docbase="NUMILOG", scenario="NUMILOG_LIBRARY",
        source_credential=CREDENTIAL, source_collection="library",
        owned=True, downloadable=True, download_url=None,
    )


def private_catalogue_masquerade():
    return replace(
        private_purchase(), source_collection="public_catalog",
        scenario="NUMILOG_PUBLIC", owned=True, downloadable=True,
    )


class NumilogOfferFixture:
    def __init__(self, *, identity="alice", accounts=None):
        self.current_identity = NS(identity_id=identity)
        self.accounts = (
            [NS(identity_id="alice", provider_id="numilog", credential_id=CREDENTIAL)]
            if accounts is None else accounts
        )
        self.session = NS(capabilities=NS(direct_download=True))
        self.source_calls = []
        self.acquisition = NS(
            override_command=lambda *_: pytest.fail("no acquisition fallback"),
            acquisition_url=lambda *_: pytest.fail("no URL resolution"),
        )

    def _session_for_source(self, provider, credential):
        self.source_calls.append((provider, credential))
        return self.session

    def can_direct(self, item):
        return LibraryService.can_direct_download(self, item)


def test_private_verified_inventory_offer_preserves_manual_button_hint():
    service = NumilogOfferFixture()
    assert service.can_direct(private_purchase()) is True
    assert service.source_calls == [("numilog", CREDENTIAL)]


@pytest.mark.parametrize("changes", [
    {"source_provider": "numilog-catalog"},
    {"source_provider": "numilog-catalog", "source_credential": "__public__"},
    {"scenario": "NUMILOG_PUBLIC"},
    {"scenario": "EBOOKS"},
    {"source_collection": "catalog"},
    {"source_collection": "public_catalog"},
    {"source_collection": None},
    {"source_credential": "__public__"},
    {"source_credential": ""},
    {"owned": False},
    {"owned": None},
    {"owned": 1},
    {"downloadable": None},
    {"downloadable": False},
    {"downloadable": 1},
    {"download_url": "https://numilog.invalid/private?token=secret"},
    {"medium": "physical"},
    {"record_id": "42"},
    {"record_id": "numilog:other-account:42:aaaaaaaaaaaaaaaa"},
    {"metadata": {"search_provenance": "known_catalog_stale"}},
])
def test_public_and_unscoped_numilog_never_resolve_provider_or_offer_download(changes):
    service = NumilogOfferFixture()
    invalid = replace(private_purchase(), **changes)
    assert service.can_direct(invalid) is False
    assert service.source_calls == []


@pytest.mark.parametrize("identity,accounts", [
    ("bob", [NS(identity_id="alice", provider_id="numilog", credential_id=CREDENTIAL)]),
    ("", [NS(identity_id="alice", provider_id="numilog", credential_id=CREDENTIAL)]),
    ("alice", []),
    ("alice", [
        NS(identity_id="alice", provider_id="numilog", credential_id=CREDENTIAL),
        NS(identity_id="bob", provider_id="numilog", credential_id=CREDENTIAL),
    ]),
    ("alice", [NS(identity_id="bob", provider_id="numilog", credential_id=CREDENTIAL)]),
])
def test_private_offer_requires_one_account_of_the_selected_identity(identity, accounts):
    service = NumilogOfferFixture(identity=identity, accounts=accounts)
    assert service.can_direct(private_purchase()) is False
    assert service.source_calls == []


def test_search_ui_never_asks_direct_broker_for_a_public_like_private_notice():
    host = _host()
    bad = private_catalogue_masquerade()
    host._remote_search_results = [bad]
    host.service.can_direct_download = Mock(return_value=True)
    host._populate_search([bad], announce=False)
    row = host.search_page.show_rows.call_args.args[0][0]
    assert row.options["action_label"] is None
    assert row.options["action_callback"] is None
    assert "Mes achats" not in row.meta
    host.service.can_direct_download.assert_not_called()
    host.download_direct_item.assert_not_called()


def test_real_private_inventory_keeps_opt_in_search_button_but_not_local_file():
    host = _host()
    book = private_purchase()
    host._remote_search_results = [book]
    host.service.can_direct_download = Mock(return_value=True)
    host._populate_search([book], announce=False)
    row = host.search_page.show_rows.call_args.args[0][0]
    assert row.options["action_label"] == "Vérifier et télécharger"
    assert callable(row.options["action_callback"])
    assert "Mes achats Numilog" in row.meta
    assert "offre à vérifier" in row.meta
    assert "Fichier local disponible" not in row.meta
    host.service.can_direct_download.assert_called_once_with(book)
    host.download_direct_item.assert_not_called()
    row.options["action_callback"](None)
    host.download_direct_item.assert_called_once_with(book, None)


def _direct_download_method():
    source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    klass = next(n for n in ast.parse(source.read_text()).body
                 if isinstance(n, ast.ClassDef) and n.name == "DetailPanel")
    fn = next(n for n in klass.body if isinstance(n, ast.FunctionDef)
              and n.name == "_direct_download_action")
    tree = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        fn,
    ], type_ignores=[]))
    env = {"result_row": result_row, "file_format": file_format}
    exec(compile(tree, str(source), "exec"), env)
    return env["_direct_download_action"]


def test_detail_never_offers_download_for_catalogue_even_if_legacy_broker_says_yes():
    row = private_catalogue_masquerade()
    service = NS(can_direct_download=Mock(return_value=True))
    window = NS(service=service, action_button=Mock(), download_direct_item=Mock())
    panel = NS(window=window, _actions=Mock())
    _direct_download_method()(panel, row)
    service.can_direct_download.assert_not_called()
    window.action_button.assert_not_called()
    panel._actions.assert_not_called()


def test_detail_retains_private_purchase_action_when_broker_positive():
    purchase = private_purchase()
    button = NS(connect=Mock())
    service = NS(can_direct_download=Mock(return_value=True), content_type_for=lambda _: "ebook")
    window = NS(service=service, action_button=Mock(return_value=button),
                download_direct_item=Mock())
    panel = NS(window=window, _actions=Mock())
    _direct_download_method()(panel, purchase)
    service.can_direct_download.assert_called_once_with(purchase)
    window.action_button.assert_called_once()
    assert window.action_button.call_args.args[0] == "Vérifier et télécharger"
    button.connect.assert_called_once()
    panel._actions.assert_called_once_with([button])
    window.download_direct_item.assert_not_called()


def test_private_ownership_is_independent_of_local_offer_qualification():
    wrong_scope = replace(private_purchase(), downloadable=False)
    assert result_row(wrong_scope)["state"] == "remote_owned"
    assert NumilogOfferFixture().can_direct(wrong_scope) is False
