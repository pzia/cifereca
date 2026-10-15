"""P0 #980: explicit, safe, per-source sync scope in GTK projections."""
from types import SimpleNamespace as NS

from cifereca.gui.sync_view import sync_source_presentation


def source(**updates):
    values = dict(provider_id="paris", label="Paris", enabled=True,
                  connected=True, sync_capable=True, sync_capabilities=(),
                  status="idle", error="", last_success="", last_attempt="")
    values.update(updates)
    return NS(**values)


def test_paris_scope_is_attested_by_capabilities():
    result = sync_source_presentation(source(sync_capabilities=("entitlements",)))
    assert "Portée de cette source : droits et emprunts." in result.summary
    assert "inventaire distant" not in result.summary
    assert "ne lance pas la synchronisation globale au démarrage" in result.summary
    assert result.action_enabled


def test_kobo_scope_remains_notices_only_and_no_acquisition():
    result = sync_source_presentation(source(
        provider_id="kobo", label="Kobo",
        sync_capabilities=("remote_inventory", "entitlements", "local_materialization"),
    ))
    assert "inventaire distant, droits et emprunts" in result.summary
    assert "ne télécharge aucun ebook" in result.summary
    assert "Acquisition locale Kobo disponible sur commande dédiée" in result.summary
    assert result.action_label == "Mettre à jour les notices Kobo en ligne"


def test_public_catalogue_is_not_described_as_a_failed_sync():
    result = sync_source_presentation(source(
        provider_id="gutenberg", label="Catalogue public",
        enabled=False, connected=True, sync_capable=False,
    ))
    assert result.sync_label == "Non prise en charge"
    assert "Catalogue public consultable indépendamment" in result.summary
    assert "ne fournit pas de synchronisation" in result.disabled_reason
    assert not result.action_enabled
    assert "Échec" not in result.summary


def test_no_provider_supplied_capabilities_or_scope_text_is_echoed():
    secret = "https://private.invalid/?token=SECRET"
    result = sync_source_presentation(source(
        sync_capabilities=(secret, "entitlements"),
    ))
    assert secret not in result.summary
    assert "Portée de cette source : droits et emprunts." in result.summary


def test_legacy_capabilities_absent_still_reports_conservative_scope():
    result = sync_source_presentation(source(sync_capabilities=None))
    assert "données en ligne prises en charge" in result.summary
    assert "inventaire distant" not in result.summary
