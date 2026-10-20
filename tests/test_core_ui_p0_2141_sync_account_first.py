"""#2141: account-first Synchronisation without ghost catalogue actions."""
from __future__ import annotations

from tests import test_core_ui_1347_sync_action_scope as harness


def test_sync_accounts_precede_catalog_only_sources_and_keep_original_account_order():
    sources = [
        harness._source(provider_id="gallica", source_id="gallica", label="Gallica",
                        sync_capable=False, sync_capabilities=()),
        harness._source(provider_id="kobo", source_id="kobo-a", label="Kobo A",
                        sync_capable=True, sync_capabilities=("remote_inventory",)),
        harness._source(provider_id="gutenberg", source_id="gutenberg",
                        label="Gutenberg", sync_capable=False, sync_capabilities=()),
        harness._source(provider_id="paris", source_id="paris-a", label="Paris A",
                        sync_capable=True, sync_capabilities=("remote_inventory",)),
    ]
    window, _broker, calls, _build, _page = harness._fixture(sources=sources)
    groups = window._sync_sources_box.children
    assert [group.children[0].title for group in groups] == [
        "Kobo A", "Paris A", "Gallica", "Gutenberg",
    ]
    assert "4 source(s) · 2 synchronisable(s)" in window._sync_summary.label
    for group in groups[2:]:
        row = group.children[0]
        assert row.subtitle == (
            "Recherche de catalogue distincte, si disponible · Activée · Connectée\n"
            "Inventaire synchronisé non pris en charge"
        )
        assert len(row.suffixes) == 1  # activation, no false sync command
    assert window.runner.tasks == []
    assert not any(entry[0] in {"sync-source", "sync-all", "preview"} for entry in calls)
    # A passive refresh preserves grouping and never issues an acquisition.
    window._refresh_sync_page()
    assert [g.children[0].title for g in window._sync_sources_box.children] == [
        "Kobo A", "Paris A", "Gallica", "Gutenberg",
    ]
    assert window.runner.tasks == []


def test_catalogue_only_activation_remains_available_without_sync_or_raw_errors():
    sources = [harness._source(
        provider_id="gallica", source_id="catalogue", label="Gallica",
        sync_capable=False, sync_capabilities=(), enabled=False, connected=False,
        error="https://private.invalid/?token=SECRET_2141",
    )]
    window, _broker, calls, _build, _page = harness._fixture(sources=sources)
    row = window._sync_sources_box.children[0].children[0]
    assert "Désactivée · Déconnectée" in row.subtitle
    assert "SECRET_2141" not in row.subtitle
    assert len(row.suffixes) == 1
    row.suffixes[0].toggled(True)
    assert ("set-enabled", "catalogue", True) in calls
    assert window.runner.tasks == []
    assert not window._sync_all_button.sensitive
