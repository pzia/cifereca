"""P1 #1889: Synchronisation suffix controls keep their natural GTK height.

The actual page controller runs with the existing broker/action doubles.
GTK allocation screenshots at 760/1366/1600 remain an independent E2E gate.
"""
from __future__ import annotations

from tests import test_core_ui_1347_sync_action_scope as harness


def _capture_alignment(monkeypatch):
    monkeypatch.setattr(
        harness.Widget, "set_valign",
        lambda self, align: setattr(self, "valign", align),
    )


def _check_all_suffixes(window):
    groups = window._sync_sources_box.children
    assert groups
    checked = []
    for group in groups:
        for row in group.children:
            for suffix in row.suffixes:
                assert getattr(suffix, "valign", None) == 3, (
                    getattr(row, "title", None), getattr(suffix, "label", None)
                )
                checked.append(getattr(suffix, "label", None))
    return checked


def test_kobo_actions_are_centered_even_with_multiline_subtitles(monkeypatch):
    _capture_alignment(monkeypatch)
    source = harness._source(label="Compte Kobo avec un nom suffisamment long")
    window, _broker, calls, _build, _page = harness._fixture(sources=[source])
    group = window._sync_sources_box.children[0]

    # Source switch, refresh, preview and audit all stay on the same row.
    assert len(group.children[0].suffixes) == 4
    # Explicit local acquisition, cancel, and both independent policy controls.
    assert len(group.children[3].suffixes) == 2
    assert len(group.children[4].suffixes) == 1
    assert len(group.children[5].suffixes) == 1
    assert len(_check_all_suffixes(window)) == 8
    assert window.runner.tasks == []
    assert not any(call[0] in {"sync-source", "acquire", "prepare"} for call in calls)


def test_non_kobo_sources_and_disabled_actions_remain_centered(monkeypatch):
    _capture_alignment(monkeypatch)
    sources = [
        harness._source(
            provider_id="numilog", source_id="numilog-one",
            label="Compte Numilog", enabled=False, connected=False,
            sync_capabilities=("remote_inventory",),
        ),
        harness._source(
            provider_id="paris", source_id="paris-one",
            label="Bibliothèques de Paris",
            sync_capabilities=("remote_inventory",),
        ),
        harness._source(
            provider_id="opds", source_id="opds-one",
            label="Catalogue OPDS", sync_capabilities=(), sync_capable=False,
        ),
    ]
    window, _broker, _calls, _build, _page = harness._fixture(sources=sources)
    # Catalogue-only OPDS keeps the activation switch, but no longer shows
    # a large disabled Synchroniser button (#2141).
    assert [len(g.children[0].suffixes) for g in window._sync_sources_box.children] == [2, 2, 1]
    labels = _check_all_suffixes(window)
    assert len(labels) == 9
    # Numilog backup consent is a distinct, centered, default-disabled action.
    numilog_group = next(
        group for group in window._sync_sources_box.children
        if group.children[0].title == "Compte Numilog"
    )
    assert numilog_group.children[2].title == "Sauvegarde automatique des achats Numilog"
    assert not numilog_group.children[2].suffixes[0].sensitive


def test_refresh_recreates_centered_controls_without_changing_actions(monkeypatch):
    _capture_alignment(monkeypatch)
    window, _broker, calls, _build, _page = harness._fixture()
    old_refresh = harness._source_row(window).suffixes[1]
    assert old_refresh.sensitive
    window._refresh_sync_page()
    labels = _check_all_suffixes(window)
    assert "Aperçu local…" in labels
    assert "Vérifier les fichiers…" in labels
    assert window.runner.tasks == []
    old_refresh.click()
    assert window.runner.tasks == []
    assert not any(call[0] == "sync-source" for call in calls)
