"""#2385: catalogues and personal-library sync are distinct UI operations.

The real GTK method is executed with headless doubles, no provider/network,
no fabricated Account, no acquisition, no mutation or permission change.
"""
from __future__ import annotations

from cifereca.plugin_api.models import CatalogueSourceCapability as Cap
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def _cap(provider, *, scope="public", available=True, refresh="unsupported",
         search=True, personal=False):
    return Cap(
        provider_id=provider, identity_scope=scope,
        catalog_search_supported=search, catalog_search_available=available,
        catalog_refresh=refresh, personal_inventory_supported=personal,
        personal_inventory_available=False,
    )


def _disclosure(window):
    return next((item for item in window._sync_sources_box.children
                 if str(getattr(item, "label", "") or "").startswith("Catalogues · recherche")),
                None)


def test_public_catalogue_without_synthetic_account_gets_read_only_search_card():
    window, broker, calls, _build, _page = _fixture(sources=[])
    inventory_calls = []
    window.service.catalogue_source_capabilities = lambda *, identity_id: (
        inventory_calls.append(identity_id) or [
            _cap("ebooksgratuits"),
            _cap("gutenberg", available=False),
        ]
    )
    window._refresh_sync_page()
    disclosure = _disclosure(window)
    assert disclosure is not None
    assert disclosure.label == "Catalogues · recherche (2)"
    assert disclosure.expanded is False
    group = disclosure.child
    assert "aucune actualisation d’index" in group.title
    assert [row.title for row in group.children] == ["ebooksgratuits", "gutenberg"]
    assert "Catalogue public · Recherche disponible" in group.children[0].subtitle
    assert "Recherche indisponible" in group.children[1].subtitle
    assert all("Synchronisation de catalogue non prise en charge" in row.subtitle
               for row in group.children)
    assert all(row.suffixes == [] for row in group.children)
    assert inventory_calls == ["alice"] and window.runner.tasks == []
    assert all(name == "sources" for name, *_ in calls)


def test_real_store_account_remains_first_and_catalogue_does_not_create_purchases():
    window, broker, calls, _build, _page = _fixture(
        sources=[_source(provider_id="kobo", sync_capable=True)],
    )
    window.service.catalogue_source_capabilities = lambda *, identity_id: (
        _cap("kobo", scope="authenticated", personal=True),
        _cap("numilog-catalog", available=False),
    )
    window._refresh_sync_page()
    assert window._sync_sources_box.children[0].children[0].title == "Kobo"
    disclosure = _disclosure(window)
    assert disclosure is not None
    row = disclosure.child.children[0]
    assert row.title == "kobo"
    assert "Catalogue avec compte" in row.subtitle
    assert row.suffixes == []
    assert window.runner.tasks == []
    assert not any(name.startswith(("sync-source", "download")) for name, *_ in calls)


def test_disabled_or_unattested_sources_never_claim_catalogue_refresh():
    window, broker, calls, _build, _page = _fixture(sources=[])
    window.service.catalogue_source_capabilities = lambda *, identity_id: (
        _cap("disabled", search=False),
        _cap("unattested", refresh="snapshot"),
        _cap("https://evil.invalid/?token=PRIVATE"),
        _cap("technical", scope="unexpected"),
    )
    window._refresh_sync_page()
    assert _disclosure(window) is None
    assert "PRIVATE" not in repr(window._sync_sources_box.children)
    assert window.runner.tasks == []


def test_catalogue_listing_refreshes_per_identity_not_across_close():
    window, broker, calls, _build, _page = _fixture(sources=[])
    window.service.catalogue_source_capabilities = (
        lambda *, identity_id: (_cap("gutenberg"),) if identity_id == "alice"
        else (_cap("ebooksgratuits"),)
    )
    window._refresh_sync_page()
    assert _disclosure(window).child.children[0].title == "gutenberg"
    window.service.current_identity.identity_id = "bob"
    window._refresh_sync_page()
    assert _disclosure(window).child.children[0].title == "ebooksgratuits"
    window._closed = True
    window._refresh_sync_page()
    assert window.runner.tasks == []


def test_p0_2882_paris_catalogue_visible_and_unavailable_without_account_card():
    window, broker, calls, _build, _page = _fixture(sources=[])
    window.service.catalogue_source_capabilities = lambda *, identity_id: (
        _cap("paris", scope="authenticated", available=False),
        _cap("gutenberg", scope="public", available=True),
    ) if identity_id == "alice" else ()
    window._refresh_sync_page()
    disclosure = _disclosure(window)
    assert disclosure is not None
    assert "Bibliothèques de Paris non connectées" in disclosure.label
    assert disclosure.expanded is False
    rows = {row.title: row for row in disclosure.child.children}
    assert "Bibliothèques de Paris" in rows
    assert "Recherche indisponible" in rows["Bibliothèques de Paris"].subtitle
    assert rows["Bibliothèques de Paris"].suffixes == []
    assert window.runner.tasks == []
    assert all(name == "sources" for name, *_ in calls)

    # Identity switching must not retain the other profile's catalogue status.
    window.service.current_identity.identity_id = "bob"
    window._refresh_sync_page()
    assert _disclosure(window) is None


def test_p0_2882_paris_ready_catalogue_visible_without_fake_sync():
    window, broker, calls, _build, _page = _fixture(sources=[])
    window.service.catalogue_source_capabilities = lambda *, identity_id: (
        _cap("paris", scope="authenticated", available=True),
    )
    window._refresh_sync_page()
    disclosure = _disclosure(window)
    assert disclosure is not None
    assert "Bibliothèques de Paris disponibles" in disclosure.label
    assert disclosure.expanded is False
    assert disclosure.child.children[0].title == "Bibliothèques de Paris"
    assert window.runner.tasks == []
