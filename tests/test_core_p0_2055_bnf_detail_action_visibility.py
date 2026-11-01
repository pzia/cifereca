"""#2055: BnF manual borrow visibility is not a catalogue quota/offer veto.

Pure CoreUI policy tests need no GTK display and perform zero provider I/O.
The actual action still requires Core.can_borrow_bnf and explicit consent.
"""
from dataclasses import replace
from pathlib import Path
import ast

import pytest

from cifereca.gui.detail_model import search_detail_borrow_action_candidate
from tests.test_core_p0_2055_manual_dispatch import notice
from cifereca.models import Account


ACCOUNT = Account(
    identity_id="default", provider_id="bnf-pret",
    credential_id="synthetic", unique_id="reader",
)
BNF = notice(ACCOUNT)


@pytest.mark.parametrize("label", [
    "", "—", "Inconnue", "Indisponible", "À confirmer",
    "Non vérifiable", "Empruntable",
])
def test_bnf_manual_action_section_ignores_unverified_catalogue_label(label):
    assert search_detail_borrow_action_candidate(BNF, label) is True


@pytest.mark.parametrize("provider", [
    "paris", "provider-reference", "bnf-test", "public-provider",
])
def test_other_lending_sources_still_require_empruntable_label(provider):
    item = replace(BNF, source_provider=provider)
    assert search_detail_borrow_action_candidate(item, "Inconnue") is False
    assert search_detail_borrow_action_candidate(item, "Empruntable") is True


@pytest.mark.parametrize("provider", ["numilog", "numilog-catalog", "kobo"])
def test_store_catalogues_do_not_inherit_bnf_manual_exception(provider):
    item = replace(BNF, source_provider=provider)
    assert search_detail_borrow_action_candidate(item, "Empruntable") is False
    assert search_detail_borrow_action_candidate(item, "Inconnue") is False


def test_bnf_non_digital_edition_never_offers_manual_lending():
    item = replace(BNF, medium="physical")
    assert search_detail_borrow_action_candidate(item, "Empruntable") is False


def test_real_detail_panel_uses_policy_and_core_button_guard():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/gui/detail_panel.py"
    ).read_text(encoding="utf-8")
    module = ast.parse(source)
    panel = next(node for node in module.body
                 if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    show = next(node for node in panel.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "show_item_detail")
    calls = [
        ast.unparse(node.func)
        for node in ast.walk(show) if isinstance(node, ast.Call)
    ]
    assert "search_detail_borrow_action_candidate" in calls
    assert "self.window.can_borrow_item" in calls
    # The BnF-specific policy must never call a remote provider.
    assert not any("borrower_eligibility_for" in name for name in calls)


@pytest.mark.parametrize("missing", [
    {"docbase": "OTHER"}, {"docbase": ""},
    {"record_id": ""}, {"record_id": None},
])
def test_bnf_catalogue_marker_cannot_substitute_exact_book_binding(missing):
    item = replace(BNF, **missing)
    assert search_detail_borrow_action_candidate(item, "Empruntable") is False


def test_malformed_non_search_result_cannot_offer_manual_bnf_action():
    from types import SimpleNamespace
    item = SimpleNamespace(source_provider="bnf-pret", medium="digital", docbase="BNF_PRET", record_id="abc")
    assert search_detail_borrow_action_candidate(item, "Empruntable") is False
