"""#980: source identity is provider-qualified, privacy-safe and stable."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import (
    sync_source_identity_label, sync_source_presentation, sync_source_row_labels,
)


def _source(provider, label, **changes):
    data = dict(
        provider_id=provider, label=label, enabled=True, connected=True,
        sync_capable=True, status="idle", last_success="", last_attempt="",
        error="",
    )
    data.update(changes)
    return NS(**data)


def test_homonymous_public_catalogues_are_visibly_distinct():
    gutenberg = _source("gutenberg", "Catalogue public")
    gallica = _source("gallica", "Catalogue public")
    assert sync_source_identity_label(gutenberg) == "Catalogue public · gutenberg"
    assert sync_source_identity_label(gallica) == "Catalogue public · gallica"
    for source in (gutenberg, gallica):
        detail = sync_source_presentation(source)
        assert detail.action_label == "Synchroniser Catalogue public"
        assert "Synchronisation : Jamais synchronisée" in detail.summary


def test_identical_provider_and_label_is_not_repeated():
    assert sync_source_identity_label(_source("paris", "PARIS")) == "PARIS"
    assert sync_source_identity_label(_source("gutenberg", "")) == "gutenberg"


@pytest.mark.parametrize("invalid", ["../private", "https://host/path", "a b", "", "x" * 65, None, 123])
def test_provider_is_strict_public_slug_not_arbitrary_sensitive_data(invalid):
    source = _source(invalid, "Catalogue public", account_id="PRIVATE_ACCOUNT")
    title = sync_source_identity_label(source)
    assert title == "Catalogue public · fournisseur inconnu"
    assert "PRIVATE_ACCOUNT" not in title
    if invalid:
        assert str(invalid) not in title


def test_private_account_and_source_ids_are_never_displayed():
    source = _source(
        "paris", "Bibliothèque", account_id="PRIVATE_ACCOUNT",
        credential_id="PRIVATE_CREDENTIAL", source_id="PRIVATE_SOURCE",
        identity_id="PRIVATE_IDENTITY",
    )
    title = sync_source_identity_label(source)
    presentation = sync_source_presentation(source)
    assert title == "Bibliothèque · paris"
    combined = title + presentation.action_label + presentation.summary
    for secret in ("PRIVATE_ACCOUNT", "PRIVATE_CREDENTIAL", "PRIVATE_SOURCE", "PRIVATE_IDENTITY"):
        assert secret not in combined


def test_disabled_public_catalogue_has_clear_reason_and_provider():
    source = _source("gallica", "Catalogue public", sync_capable=False)
    presentation = sync_source_presentation(source)
    assert presentation.action_enabled is False
    assert presentation.sync_label == "Non prise en charge"
    assert "ne fournit pas de synchronisation" in presentation.summary
    assert "gallica" in sync_source_identity_label(source)


def test_gtk_sync_row_and_worker_use_same_safe_identity_projection():
    file = Path(__file__).resolve().parents[1] / "src/cifereca/gui/sync_controller.py"
    tree = ast.parse(file.read_text(encoding="utf-8"))
    refresh = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == "_refresh_sync_page")
    source = ast.unparse(refresh)
    assert "row.set_title(display_label)" in source
    assert "label=display_label" in source
    assert "row_titles = sync_source_row_labels(rows)" in source
    assert "row.set_title(source.label or source.provider_id)" not in source


def test_gtk_controller_titles_disambiguate_public_catalogues_only_when_same_name():
    from tests.test_core_ui_1347_sync_action_scope import _fixture, _source
    sources = [
        _source(provider_id="gutenberg", source_id="public-g", label="Catalogue public",
                sync_capable=False, enabled=False),
        _source(provider_id="gallica", source_id="public-b", label="Catalogue public",
                sync_capable=False, enabled=False),
        _source(provider_id="paris", source_id="card-1", label="Paris · Camille"),
    ]
    window, broker, calls, _build, _page = _fixture(sources=sources)
    rows = [group.children[0] for group in window._sync_sources_box.children]
    # #2141: genuine synchronisable accounts are presented before public
    # catalogues; provider disambiguation remains in the public titles.
    assert [row.title for row in rows] == [
        "Paris · Camille",
        "Catalogue public · gutenberg",
        "Catalogue public · gallica",
    ]
    assert "Fournisseur : paris" in rows[0].subtitle
    for row in rows[1:]:
        assert "Catalogue consultable" in row.subtitle
        assert "Synchronisation des données non prise en charge" in row.subtitle
        assert len(row.suffixes) == 1  # activation switch; no ghost sync
    assert window.runner.tasks == []
    assert all(name == "sources" for name, *_ in calls)


def test_gtk_controller_action_and_task_preserve_single_non_kobo_source_name():
    from tests.test_core_ui_1347_sync_action_scope import _fixture, _source
    source = _source(provider_id="paris", source_id="card-1", label="Paris · Camille")
    window, broker, calls, _build, _page = _fixture(sources=[source])
    row = window._sync_sources_box.children[0].children[0]
    assert row.title == "Paris · Camille"
    assert row.subtitle.startswith("Fournisseur : paris")
    button = row.suffixes[1]
    assert button.label == "Synchroniser Paris · Camille"
    button.click()
    assert window.runner.tasks[0].label == "Synchronisation Paris · Camille…"
    assert not any(name == "sync-source" for name, *_ in calls)


def test_same_provider_same_title_uses_only_ephemeral_connection_ordinals():
    sources = [
        _source("paris", "Catalogue des prêts", source_id="PRIVATE_FIRST",
                account_id="PRIVATE_ACCOUNT_1"),
        _source("paris", "Catalogue des prêts", source_id="PRIVATE_SECOND",
                account_id="PRIVATE_ACCOUNT_2"),
        _source("gallica", "Autres livres"),
    ]
    assert sync_source_row_labels(sources) == (
        "Catalogue des prêts · paris · connexion 1/2",
        "Catalogue des prêts · paris · connexion 2/2",
        "Autres livres",
    )
    assert all("PRIVATE_" not in label for label in sync_source_row_labels(sources))
    assert sync_source_row_labels(sources[:1]) == ("Catalogue des prêts",)


def test_mixed_case_homonyms_are_disambiguated_without_mutating_sources():
    sources = [
        _source("gutenberg", "Catalogue public", source_id="private-a"),
        _source("gallica", "catalogue PUBLIC", source_id="private-b"),
    ]
    assert sync_source_row_labels(sources) == (
        "Catalogue public · gutenberg", "catalogue PUBLIC · gallica",
    )
    assert sources[0].label == "Catalogue public"


def test_same_provider_and_label_can_keep_per_source_routing():
    from tests.test_core_ui_1347_sync_action_scope import _fixture, _source
    sources = [
        _source(provider_id="paris", source_id="card-first",
                label="Paris · Mon compte"),
        _source(provider_id="paris", source_id="card-second",
                label="Paris · Mon compte"),
    ]
    window, broker, calls, _build, _page = _fixture(sources=sources)
    groups = window._sync_sources_box.children
    rows = [group.children[0] for group in groups]
    assert [row.title for row in rows] == [
        "Paris · Mon compte · paris · connexion 1/2",
        "Paris · Mon compte · paris · connexion 2/2",
    ]
    assert [row.suffixes[1].label for row in rows] == [
        "Synchroniser Paris · Mon compte",
        "Synchroniser Paris · Mon compte",
    ]
    rows[1].suffixes[1].click()
    assert window.runner.tasks[0].label == (
        "Synchronisation Paris · Mon compte · paris · connexion 2/2…"
    )
    assert window.runner.tasks[0].work() == "ok"
    assert ("sync-source", "card-second") in calls
    assert ("sync-source", "card-first") not in calls
