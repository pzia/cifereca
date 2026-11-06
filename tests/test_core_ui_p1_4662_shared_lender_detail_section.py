"""#4662: Search and Recommendations can share one inert 0/1/N detail model.

This deliberately tests the public DetailSection contract without GTK or a
source session. Even a positive synthetic opportunity cannot create an action.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import ast

from cifereca.core.borrow_for_identity_detail import (
    cross_profile_borrow_detail_section,
)
from cifereca.plugin_api.models import DetailSection, DetailField
from tests.test_core_p1_4662_cross_profile_borrow_preview import (
    project, witness,
)


def test_zero_offers_does_not_invent_absence_of_every_lender_right():
    preview = project()
    section = cross_profile_borrow_detail_section(preview)
    assert type(section) is DetailSection
    assert section.title == "Emprunt entre profils · information"
    assert len(section.fields) == 1
    assert section.fields[0].label == "État"
    assert "non vérifiées" in section.fields[0].value
    assert "aucun droit" not in section.fields[0].value.casefold()
    assert not hasattr(section, "actions")


def test_one_own_account_keeps_a_non_actionable_summary():
    preview = project(witness("bob"))
    section = cross_profile_borrow_detail_section(preview)
    assert len(section.fields) == 2
    assert "Profil actif" in section.fields[1].value
    assert "transfert non vérifié" in section.fields[1].value
    assert "Emprunter" not in section.fields[1].value
    assert all(type(field) is DetailField and field.kind == "text"
               for field in section.fields)


def test_external_lender_explicitly_shows_transfer_blocked_without_a_copy():
    preview = project(witness("alice", transfer="blocked"))
    section = cross_profile_borrow_detail_section(preview)
    assert len(section.fields) == 2
    assert "Alice" in section.fields[1].value
    assert "transfert bloqué" in section.fields[1].value
    assert not any("option_id" in field.id for field in section.fields)
    assert not hasattr(section, "callback")


def test_multiple_accounts_stay_distinguishable_and_unactionable():
    preview = project(
        witness("alice", "card-A", "account-A"),
        witness("alice", "card-B", "account-B"),
        witness("charlie", "card-C", "account-C"),
    )
    section = cross_profile_borrow_detail_section(preview)
    assert len(section.fields) == 4
    assert [f.label for f in section.fields[1:]] == [
        "Choix 1/3", "Choix 2/3", "Choix 3/3",
    ]
    assert len(set(field.value for field in section.fields[1:])) == 2
    assert all("card-" not in field.value for field in section.fields)
    assert all("account-" not in field.value for field in section.fields)


def test_private_profile_and_account_labels_are_not_exposed():
    original = witness("alice", "sensitive-card", "secret-account")
    fake = replace(
        original,
        lender_name="alice@example.com",
        account=replace(
            original.account, display_name="1234567890123",
        ),
    )
    section = cross_profile_borrow_detail_section(project(fake))
    text = " ".join(str(field.value) for field in section.fields)
    assert "example.com" not in text
    assert "1234567890123" not in text
    assert "sensitive-card" not in text
    assert "secret-account" not in text
    assert "Profil prêteur" in text


def test_invalid_or_forged_authorizing_dto_produces_no_detail_action():
    preview = project(witness("alice"))
    assert cross_profile_borrow_detail_section(None) is None
    assert cross_profile_borrow_detail_section(
        replace(preview, executable=True),
    ) is None
    assert cross_profile_borrow_detail_section(
        replace(preview, status="choose"),
    ) is None
    assert cross_profile_borrow_detail_section(
        replace(preview, options=(
            replace(preview.options[0], executable=True),
        )),
    ) is None


def test_standard_search_detail_has_opt_in_no_eager_provider_observation():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/gui/detail_panel.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    existing = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "show_item_detail"
    )
    assert existing.args.kwonlyargs == []  # Stable public GUI facade.
    helper = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "show_cross_profile_borrow_detail"
    )
    assert {a.arg for a in helper.args.kwonlyargs} == {
        "expected_item", "expected_edition_id",
        "expected_context_epoch", "expected_source_epoch",
    }
    names = [
        node.func.id for node in ast.walk(helper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "cross_profile_borrow_detail_section" in names
    assert "holdings" not in names
    assert "borrow_paris_manual" not in names
    assert "borrow_search_item" not in names
