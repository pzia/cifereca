"""P1 #3732 — generic Sync CoreUI canaries for an installable future provider.

The actual Sync controller is executed through the established headless GTK
fixture. No provider implementation, network or canonical acquisition API is
called. These tests establish a stable seam for the CoreUI cleanup #3416.
"""
from types import SimpleNamespace as NS

from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def _future(source_id, identity_id="alice", **changes):
    return _source(
        identity_id=identity_id, source_id=source_id,
        provider_id="reader.future-v2", label="Bibliothèque fictive",
        sync_capabilities=("remote_inventory", "local_materialization"),
        **changes,
    )


def test_unknown_provider_card_shows_typed_phases_without_provider_tools():
    source = _future(
        "future-one", status="partial", error="Synchronisation partielle",
        phase_states=(
            NS(phase="discover_rights", status="partial",
               last_attempt="2026-10-31T12:30:00Z", last_success=""),
            NS(phase="materialize", status="success",
               last_attempt="2026-10-31T12:31:00Z",
               last_success="2026-10-31T12:31:00Z"),
        ),
    )
    window, _broker, calls, _build, _page = _fixture(sources=[source])
    group = window._sync_sources_box.children[0]
    row = group.children[0]

    assert row.title == "Bibliothèque fictive"
    assert "Notices et droits : Partielle" in row.subtitle
    assert "Téléchargement local : Réussie" in row.subtitle
    assert "couverture partielle (information)" in row.subtitle
    # No provider-specific download/loan/backup widgets appear on an
    # unrecognized provider that only publishes the generic Sync contract.
    assert len(row.suffixes) == 2
    assert not any("Kobo" in str(getattr(item, "title", ""))
                   or "Numilog" in str(getattr(item, "title", ""))
                   for item in group.children)
    assert window.runner.tasks == []
    assert calls == [("sources", "alice")]


def test_unknown_provider_toggle_is_scoped_without_implicit_download():
    sources = [
        _future("future-first"),
        _source(
            source_id="future-second", provider_id="reader.future-v2",
            label="Second test",
            sync_capabilities=("remote_inventory", "local_materialization"),
        ),
        _future("foreign-bob", identity_id="bob"),
    ]
    window, _broker, calls, _build, _page = _fixture(sources=sources)
    groups = {group._cifereca_sync_source_id: group
              for group in window._sync_sources_box.children}
    assert set(groups) == {"future-first", "future-second"}

    groups["future-first"].children[0].suffixes[0].toggled(False)
    assert ("set-enabled", "future-first", False) in calls
    assert not any(entry[0] == "set-enabled" and entry[1] == "future-second"
                   for entry in calls)
    assert not any(entry[0] in {"sync-source", "preview", "audit"} for entry in calls)
    assert window.runner.tasks == []

    # UI toggle never claims to authorize local acquisition. Explicit
    # synchronization is an entirely separate scoped task.
    groups = {group._cifereca_sync_source_id: group
              for group in window._sync_sources_box.children}
    groups["future-second"].children[0].suffixes[1].click()
    assert len(window.runner.tasks) == 1
    assert window.runner.tasks[0].work() == "ok"
    assert ("sync-source", "future-second") in calls
    assert ("sync-source", "future-first") not in calls


def test_unknown_provider_disabled_or_foreign_source_cannot_run():
    sources = [
        _future("future-disabled", enabled=False),
        _future("foreign-bob", identity_id="bob"),
    ]
    window, _broker, calls, _build, _page = _fixture(sources=sources)
    group = window._sync_sources_box.children[0]
    row = group.children[0]
    assert group._cifereca_sync_source_id == "future-disabled"
    assert row.suffixes[1].sensitive is False
    row.suffixes[1].click()
    assert window.runner.tasks == []
    assert not any(entry[0] in {"sync-source", "sync-all"} for entry in calls)
