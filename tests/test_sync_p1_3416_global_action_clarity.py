"""P1 #3416: global Sync and local refresh must communicate distinct effects.

Headless GTK controller tests; neither button is clicked by this check.
"""
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def test_global_action_is_identity_scoped_and_discloses_existing_acquisition_consent():
    window, _broker, calls, _build, page = _fixture(sources=[
        _source(provider_id="fictional.future", source_id="future"),
    ])
    action = window._sync_all_button
    assert action.label == "Actualiser les synchronisations actives · Alice"
    assert action.sensitive is True
    assert "cette identité" in action.tooltip
    assert "fichiers" in action.tooltip
    assert "autorisations déjà enregistrées" in action.tooltip
    assert "ne change aucun consentement" in action.tooltip
    # The first button only redraws in-memory state; it must not be mistaken
    # for a remote sync or an implicit local acquisition.
    refresh = page.children[0].suffixes[0]
    assert refresh.label == "Actualiser l’affichage"
    assert "sans lancer de synchronisation ni acquisition" in refresh.tooltip
    assert not window.runner.tasks
    assert calls == [("sources", "alice")]


def test_no_identity_never_enables_global_action_or_acquisition():
    window, _broker, calls, _build, _page = _fixture(identity_id=None)
    action = window._sync_all_button
    assert action.label == "Actualiser les synchronisations actives"
    assert action.sensitive is False
    assert not window.runner.tasks
    assert calls == []
