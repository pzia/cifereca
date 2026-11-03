"""#3416: catalogue search is visible but is NOT a sync permission.

Synthetic public catalogue capability objects only; no network, acquisition,
catalogue refresh, consent or personal metadata. All displayed text is closed.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_catalogue_overview import sync_catalogue_overview


def catalog(provider, *, available=True, refresh="unsupported",
            supported=True, scope="public"):
    return NS(
        provider_id=provider, identity_scope=scope,
        catalog_search_supported=supported,
        catalog_search_available=available, catalog_refresh=refresh,
    )


def test_declared_public_and_authenticated_catalogues_have_no_false_refresh():
    rows = [
        catalog("catalog.alpha"),
        catalog("catalog.beta", available=False, scope="authenticated"),
    ]
    label = sync_catalogue_overview(rows)
    assert "Catalogues : 1/2 recherche(s) disponible(s)" in label
    assert "recherche ≠ synchronisation de catalogue" in label
    assert "aucune actualisation d’index proposée" in label
    assert "catalog.alpha" not in label and "catalog.beta" not in label


def test_no_supposed_catalogue_sync_without_an_attested_contract():
    label = sync_catalogue_overview([
        catalog("future-provider", refresh="experimental"),
    ])
    assert "1 capacité(s) d’index non qualifiée(s)" in label
    assert "aucune action proposée" in label
    assert "Activé" not in label


@pytest.mark.parametrize("bad", [
    None, "any", {"catalogue": True}, [catalog("x")] * 129,
    [catalog("https://secret.invalid/?token=TOPSECRET")],
    [catalog("x", scope="private-account")],
    [catalog("x", supported="true")],
    [catalog("x", available="true")],
])
def test_malformed_or_private_capability_is_not_rendered(bad):
    label = sync_catalogue_overview(bad)
    assert label == (
        "Catalogues : état non vérifié · aucune synchronisation d’index proposée"
    )
    assert "TOPSECRET" not in label and "private-account" not in label


def test_missing_catalogue_search_does_not_prove_a_missing_provider():
    assert "aucune recherche déclarée" in sync_catalogue_overview([])
    label = sync_catalogue_overview([catalog("provider", supported=False)])
    assert "aucune recherche déclarée" in label


def test_gtk_dashboard_stays_account_scoped_and_does_no_provider_io():
    from tests.test_core_ui_1347_sync_action_scope import _fixture, _source

    window, _broker, calls, _build, _page = _fixture(
        sources=[_source(provider_id="fictional")],
    )
    read = []
    window.service.catalogue_source_capabilities = lambda *, identity_id: (
        read.append(identity_id) or [
            catalog("fictional"), catalog("future", available=False),
        ]
    )
    window._refresh_sync_page()
    assert read == ["alice"]
    assert "Catalogues : 1/2 recherche(s) disponible(s)" in window._sync_summary.label
    assert "aucune actualisation d’index proposée" in window._sync_summary.label
    assert "Notices et droits" in window._sync_summary.label
    assert calls == [("sources", "alice"), ("sources", "alice")]
    assert window.runner.tasks == []
    # Catalogues appear as read-only, separately disclosed entries.
    assert len(window._sync_sources_box.children) == 2


def test_unavailable_reader_preserves_personal_source_controls():
    from tests.test_core_ui_1347_sync_action_scope import _fixture

    window, broker, calls, _build, _page = _fixture()
    window.service.catalogue_source_capabilities = lambda *, identity_id: (
        (_ for _ in ()).throw(RuntimeError("Bearer TOPSECRET"))
    )
    window._refresh_sync_page()
    assert "Catalogues : état non vérifié" in window._sync_summary.label
    assert "TOPSECRET" not in window._sync_summary.label
    assert window._sync_sources_box.children[0].children
    assert window.runner.tasks == []


def test_identity_switch_during_reader_cannot_publish_stale_catalogue_counts():
    from tests.test_core_ui_1347_sync_action_scope import _fixture

    window, broker, calls, _build, _page = _fixture()
    def reader(*, identity_id):
        window.service.current_identity = NS(identity_id="bob")
        return [catalog("provider")]
    window.service.catalogue_source_capabilities = reader
    window._refresh_sync_page()
    assert "1/1 recherche" not in window._sync_summary.label
    assert window.runner.tasks == []
    assert not any(event[0] in {"sync-all", "sync-source"} for event in calls)


def test_legacy_numilog_status_dict_never_overwrites_gtk_summary_widget():
    # #3416 must coexist with legacy read-only Numilog status panels, whose
    # handler also assigns a variable named 'summary' in the outer method.
    from tests.test_core_p0_1905_numilog_consent_ui import _ui
    window, _broker, calls, _dialogs, _state, _row = _ui(initial="on_start")
    window._refresh_sync_page()
    assert "Catalogues : état non vérifié" in window._sync_summary.label
    assert "Notices et droits" in window._sync_summary.label
    assert not window.runner.tasks
