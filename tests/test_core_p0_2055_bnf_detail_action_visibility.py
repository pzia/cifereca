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

@pytest.mark.parametrize("provider", ["future-lender", "paris", "bnf-pret"])
def test_exact_manual_candidate_is_provider_neutral_and_advisory(provider):
    from dataclasses import replace
    item = replace(
        BNF, source_provider=provider, docbase="PROVIDER_BOOK",
        record_id="publication-7",
        metadata={"manual_borrow_presentation": {
            "provider_id": provider, "docbase": "PROVIDER_BOOK",
            "record_id": "publication-7",
        }},
    )
    assert search_detail_borrow_action_candidate(item, "Non vérifiée")
    assert not search_detail_borrow_action_candidate(
        replace(item, source_provider="another-lender"), "Non vérifiée",
    )
    assert not search_detail_borrow_action_candidate(
        replace(item, record_id="another-edition"), "Empruntable",
    )
    assert not search_detail_borrow_action_candidate(
        replace(item, docbase="another-collection"), "Empruntable",
    )


@pytest.mark.parametrize("bad", [
    None, True, "allow", {}, {"provider_id": "bnf-pret"},
    {"provider_id": "bnf-pret", "docbase": "BNF_PRET", "record_id": 1},
    {"provider_id": "bnf-pret", "docbase": "BNF_PRET", "record_id": "book-1",
     "private_url": "SECRET"},
])
def test_malformed_manual_candidate_never_raises_or_exposes_action(bad):
    item = replace(BNF, metadata={"manual_borrow_presentation": bad})
    assert not search_detail_borrow_action_candidate(item, "Empruntable")


def test_coreui_policy_has_no_bnf_specific_presentation_branch():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_model.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == "search_detail_borrow_action_candidate")
    assert not any(isinstance(node, ast.Compare)
                   and any(isinstance(value, ast.Constant) and value.value == "bnf-pret"
                           for value in (node.left, *node.comparators))
                   for node in ast.walk(func))
    assert "manual_borrow_presentation" in ast.get_source_segment(source, func)
