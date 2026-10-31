"""#3252: provider-neutral selected-offer UX, no v2 provider I/O while rendering.

A capability is not a grant. The actual action remains behind the Core
can_direct_download decision and execution-time validation.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.offer_actions import (
    download_action_label, requires_selected_offer_check,
)
from tests.test_search_results_view import _book, _host
from tests.test_core_ui_p1_1867_numilog_direct_download_scope import (
    _direct_download_method, private_purchase,
)


class _V2Session:
    def __init__(self):
        self.snapshot = Mock(side_effect=AssertionError("snapshot during GTK render"))
        self.selected = Mock(side_effect=AssertionError("offer during GTK render"))

    def downloadable_resource_snapshot(self, *args, **kwargs):
        return self.snapshot(*args, **kwargs)

    def downloadable_selected_offer_snapshot(self, *args, **kwargs):
        return self.selected(*args, **kwargs)


@pytest.mark.parametrize("provider", [
    "provider-reference", "paris", "bnf-pret", "numilog", "kobo",
])
def test_v2_qualification_is_contract_based_without_network(provider):
    session = _V2Session()
    service = NS(_session_for_source=Mock(return_value=session))
    item = replace(_book(provider), source_credential="account-a")
    assert requires_selected_offer_check(service, item) is True
    service._session_for_source.assert_called_once_with(provider, "account-a")
    session.snapshot.assert_not_called()
    session.selected.assert_not_called()


def test_unknown_or_public_source_never_becomes_eligible_from_v2_capability():
    session = _V2Session()
    service = NS(_session_for_source=Mock(return_value=session))
    item = replace(_book("provider-reference"), source_credential="__public__")
    assert requires_selected_offer_check(service, item) is False
    service._session_for_source.assert_not_called()


def test_legacy_source_keeps_direct_download_wording_without_v2_claim():
    service = NS(_session_for_source=Mock(return_value=NS(capabilities=NS(direct_download=True))))
    item = _book("public-provider")
    assert requires_selected_offer_check(service, item) is False
    assert download_action_label(item, check_required=False) == "Télécharger"


@pytest.mark.parametrize("provider,expected_label", [
    ("provider-reference", "Vérifier et télécharger"),
    ("paris", "Vérifier et télécharger"),
    ("bnf-pret", "Vérifier et télécharger"),
    ("kobo", "Vérifier et sauvegarder"),
])
def test_search_labels_and_offer_notice_are_generic_for_all_v2_sources(
    provider, expected_label,
):
    host = _host()
    session = _V2Session()
    host.service._session_for_source = Mock(return_value=session)
    host.service.can_direct_download = Mock(return_value=True)
    item = replace(_book(provider), downloadable=True, owned=True)
    if provider == "kobo":
        host.service.kobo_personal_right_states = lambda _: {("credential", "same"): "active"}
    host._remote_search_results = [item]

    host._populate_search([item], announce=False)
    rows = host.search_page.show_rows.call_args.args[0]
    assert len(rows) == 1
    row = rows[0]
    assert row.options["action_label"] == expected_label
    assert "Offre à vérifier avant acquisition" in row.meta
    assert "Fichier local disponible" not in row.meta
    session.snapshot.assert_not_called()
    session.selected.assert_not_called()
    host.download_direct_item.assert_not_called()
    row.options["action_callback"](None)
    host.download_direct_item.assert_called_once_with(item, None)


@pytest.mark.parametrize("provider", [
    "provider-reference", "paris", "bnf-pret", "numilog",
])
def test_detail_label_uses_v2_contract_instead_of_provider_allowlist(provider):
    item = private_purchase() if provider == "numilog" else replace(
        _book(provider), downloadable=True, owned=True,
    )
    session = _V2Session()
    service = NS(
        can_direct_download=Mock(return_value=True),
        _session_for_source=Mock(return_value=session),
        content_type_for=lambda _: "ebook",
    )
    button = NS(connect=Mock())
    window = NS(service=service, action_button=Mock(return_value=button),
                download_direct_item=Mock())
    panel = NS(window=window, _actions=Mock())
    _direct_download_method()(panel, item)
    assert window.action_button.call_args.args[0] == "Vérifier et télécharger"
    session.snapshot.assert_not_called()
    session.selected.assert_not_called()


def test_detail_does_not_promote_unknown_public_numilog_notice():
    row = replace(private_purchase(), source_provider="numilog-catalog")
    service = NS(
        can_direct_download=Mock(return_value=True),
        _session_for_source=Mock(return_value=_V2Session()),
    )
    window = NS(service=service, action_button=Mock(), download_direct_item=Mock())
    panel = NS(window=window, _actions=Mock())
    _direct_download_method()(panel, row)
    service.can_direct_download.assert_not_called()
    service._session_for_source.assert_not_called()
    window.action_button.assert_not_called()
