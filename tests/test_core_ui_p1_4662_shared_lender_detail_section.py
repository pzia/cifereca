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
    cross_profile_borrow_progress_detail_section,
)
from cifereca.core.borrow_for_identity_progress import CrossProfileBorrowProgress
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


def test_confirmed_lender_loan_never_presents_delivery_or_copy_as_complete():
    progress = CrossProfileBorrowProgress(
        "confirmed", "unavailable", "blocked",
        "lender_loan_confirmed_transfer_unverified",
    )
    section = cross_profile_borrow_progress_detail_section(progress)
    assert type(section) is DetailSection
    assert [field.label for field in section.fields] == [
        "Prêt", "Livraison", "Transfert",
    ]
    assert "Prêt confirmé" in section.fields[0].value
    assert "non vérifiée" in section.fields[1].value
    assert "bloquée" in section.fields[2].value
    assert "droits non vérifiés" in section.fields[2].value
    assert not hasattr(section, "actions")
    assert not hasattr(section, "callback")


def test_unknown_borrow_does_not_recommend_a_second_supplier_post():
    progress = CrossProfileBorrowProgress(
        "unknown", "unavailable", "blocked",
        "loan_result_not_confirmed",
    )
    section = cross_profile_borrow_progress_detail_section(progress)
    assert "ne pas recommencer" in section.fields[0].value
    assert section.fields[2].value.endswith("bloquée")
    assert all(field.kind == "text" for field in section.fields)


def test_absent_loan_does_not_imply_fresh_offer_or_copy_rights():
    progress = CrossProfileBorrowProgress(
        "not_attempted", "unavailable", "not_attempted",
        "loan_not_attempted",
    )
    section = cross_profile_borrow_progress_detail_section(progress)
    assert "Aucun emprunt effectué" in section.fields[0].value
    assert "Aucun transfert effectué" in section.fields[2].value
    assert "Éligible" not in repr(section)


def test_unverified_lender_scope_does_not_expose_foreign_loan_status():
    progress = CrossProfileBorrowProgress(
        "unknown", "unavailable", "blocked", "lender_scope_unverified",
    )
    section = cross_profile_borrow_progress_detail_section(progress)
    assert "statut du prêt inaccessible" in section.fields[0].value
    assert "confirmé" not in section.fields[0].value


def test_forged_or_actionable_progress_produces_no_detail_section():
    progress = CrossProfileBorrowProgress(
        "confirmed", "unavailable", "blocked",
        "lender_loan_confirmed_transfer_unverified",
    )
    assert cross_profile_borrow_progress_detail_section(None) is None
    for forged in (
        replace(progress, executable=True),
        replace(progress, retry_borrow=True),
        replace(progress, delivery="available"),
        replace(progress, transfer="completed"),
        replace(progress, reason_code="PRIVATE_LOAN_ID"),
        replace(progress, loan="unknown"),
    ):
        assert cross_profile_borrow_progress_detail_section(forged) is None


def _progress_gtk_renderer(
    method_name="show_cross_profile_borrow_progress_detail",
    on_group_title=None,
):
    """Compile the real GTK method without importing GI."""
    from types import SimpleNamespace as NS

    source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    method = next(node for node in owner.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == method_name)
    events = []

    class Group:
        def __init__(self):
            self.rows = []
        def set_title(self, title):
            self.title = title
            if on_group_title is not None:
                on_group_title()
        def add(self, row):
            self.rows.append(row)

    class Row:
        def set_use_markup(self, enabled):
            assert enabled is False
        def set_title(self, title):
            self.title = title
        def set_subtitle(self, subtitle):
            self.subtitle = subtitle

    scope = {
        "__package__": "cifereca.gui",
        "SearchResult": __import__("cifereca.models", fromlist=["SearchResult"]).SearchResult,
        "Adw": NS(PreferencesGroup=Group, ActionRow=Row),
    }
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[method], type_ignores=[])
    ), str(source), "exec"), scope)
    return scope[method_name], events


def test_gtk_progress_renders_three_nonactionable_rows_on_same_search_detail():
    from types import SimpleNamespace as NS

    render, events = _progress_gtk_renderer()
    item = witness("alice").notice
    identity = NS(identity_id="default")
    service = NS(current_identity=identity, _account_context_epoch=4,
                 _source_context_epoch=9)
    host = NS(window=NS(service=service),
              _rendered_search_detail=(item, object()),
              _append_content=lambda group: events.append(group))
    progress = CrossProfileBorrowProgress(
        "confirmed", "unavailable", "blocked",
        "lender_loan_confirmed_transfer_unverified",
    )
    assert render(host, progress, expected_item=item,
                  requesting_identity_id="default",
                  expected_context_epoch=4, expected_source_epoch=9)
    assert len(events) == 1
    assert events[0].title == "Emprunt entre profils · suivi"
    assert [row.title for row in events[0].rows] == ["Prêt", "Livraison", "Transfert"]
    assert "droits non vérifiés" in events[0].rows[2].subtitle
    assert "confirmé" in events[0].rows[0].subtitle
    assert all(not hasattr(row, "connect") for row in events[0].rows)


def test_gtk_progress_never_appends_stale_or_rebound_detail():
    from types import SimpleNamespace as NS
    from dataclasses import replace

    render, events = _progress_gtk_renderer()
    item = witness("alice").notice
    other = replace(item, record_id="different")
    service = NS(current_identity=NS(identity_id="default"),
                 _account_context_epoch=4, _source_context_epoch=9)
    host = NS(window=NS(service=service),
              _rendered_search_detail=(item, object()),
              _append_content=lambda group: events.append(group))
    progress = CrossProfileBorrowProgress(
        "unknown", "unavailable", "blocked", "loan_result_not_confirmed",
    )
    kwargs = dict(expected_item=item, requesting_identity_id="default",
                  expected_context_epoch=4, expected_source_epoch=9)
    assert render(host, progress, **{**kwargs, "expected_item": other}) is False
    assert render(host, progress, **{**kwargs, "requesting_identity_id": "alex"}) is False
    assert render(host, replace(progress, executable=True), **kwargs) is False
    service._source_context_epoch += 1
    assert render(host, progress, **kwargs) is False
    service._source_context_epoch -= 1
    service.current_identity = NS(identity_id="alex")
    assert render(host, progress, **kwargs) is False
    assert events == []


def test_gtk_lender_preview_refuses_same_id_context_rebind_mid_render():
    from types import SimpleNamespace as NS

    item = witness("alice").notice
    preview = project(witness("alice"))
    identity = NS(identity_id="bob")
    service = NS(current_identity=identity, _account_context_epoch=4,
                 _source_context_epoch=9)
    rendered = []
    host = NS(window=NS(service=service),
              _rendered_search_detail=(item, object()),
              _append_content=lambda group: rendered.append(group))

    def change_context_during_gtk_build():
        # The ID and both Core epochs stay unchanged. The context object
        # itself was replaced after the first guard and before append.
        service.current_identity = NS(identity_id=identity.identity_id)

    render, _ = _progress_gtk_renderer(
        "show_cross_profile_borrow_detail",
        on_group_title=change_context_during_gtk_build,
    )
    assert render(
        host, preview, expected_item=item, expected_edition_id="edition-1",
        expected_context_epoch=4, expected_source_epoch=9,
    ) is False
    assert rendered == []
    assert service.current_identity.identity_id == identity.identity_id
    assert service.current_identity is not identity
