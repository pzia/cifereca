"""#2385: explicit catalogue ≠ personal inventory ≠ acquisition.

No plugin activation, network, sync, download or fake public Account is needed.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import CatalogBrokerImpl
from cifereca.core.catalogue_source_inventory import (
    CatalogueSourceCapability, catalogue_source_capabilities,
)
from cifereca.providers.base import (
    ProviderCapabilities, ProviderDescriptor, CredentialField,
)
from cifereca.providers.registry import ProviderRegistry
from cifereca.services import LibraryService
from cifereca.plugin_api import (
    CatalogueSourceCapability as PublicCatalogueSourceCapability,
    CatalogueSourceInventoryBroker,
)


def _plugin(name, *, search=False, loans=False, personal=False, state="ready",
            authenticated=False):
    descriptor = ProviderDescriptor(
        id=name, display_name="Private reader name; must not be exported",
        description="Sensitive text", state=state,
        capabilities=ProviderCapabilities(
            search=search, loans=loans, personal_library=personal,
            direct_download=search,
        ),
        credential_fields=(CredentialField(
            key="login", label="private", secret=True,
        ),) if authenticated else (),
    )
    return NS(descriptor=descriptor)


def _rig():
    registry = ProviderRegistry([
        _plugin("gallica", search=True),
        _plugin("gutenberg", search=True),
        _plugin("ebooksgratuits", search=True),
        _plugin("bnf-catalogue", search=True),
        _plugin("kobo", search=True, personal=True, authenticated=True),
        _plugin("numilog", search=True, personal=True, authenticated=True),
        _plugin("numilog-catalog", search=True),  # disabled public plugin
        _plugin("paris", search=True, loans=True, authenticated=True),
        _plugin("rights-only", personal=True, authenticated=True),
        _plugin("technical", search=False, personal=False),
        _plugin("disabled", search=True, state="skeleton"),
    ])
    attempts = []
    public_sessions = [
        NS(provider_id=name, connected=True, credential_id="__public__")
        for name in ("gallica", "gutenberg", "ebooksgratuits", "bnf-catalogue")
    ]
    account_sessions = [
        NS(provider_id="kobo", credential_id="a-kobo", connected=True),
        NS(provider_id="numilog", credential_id="a-numilog", connected=True),
        NS(provider_id="paris", credential_id="a-paris", connected=False),
        NS(provider_id="rights-only", credential_id="b-only", connected=True),
    ]
    service = NS(
        registry=registry,
        current_identity=NS(identity_id="alice"),
        identities=[NS(identity_id="alice"), NS(identity_id="bob")],
        _sessions={str(i): x for i, x in enumerate(public_sessions + account_sessions)},
        _provider_enabled_for_identity=lambda name, identity: (
            attempts.append(("enabled", name, identity)) or
            name not in {"numilog-catalog", "disabled"} and
            not (identity == "bob" and name in {"kobo", "ebooksgratuits"})
        ),
        accounts_for_identity=lambda identity: (
            attempts.append(("accounts", identity)) or
            ([NS(provider_id=n, credential_id=f"a-{n}", identity_id="alice")
              for n in ("kobo", "numilog", "paris")] if identity == "alice" else
             [NS(provider_id="rights-only", credential_id="b-only",
                 identity_id="bob")])
        ),
    )
    service.catalogue_source_capabilities = (
        lambda identity_id=None: catalogue_source_capabilities(
            service, identity_id=identity_id,
        )
    )
    return service, attempts


def test_all_active_catalogues_present_even_without_public_accounts():
    service, calls = _rig()
    sources = catalogue_source_capabilities(service)
    names = [x.provider_id for x in sources]
    assert names == sorted(names)
    assert names == [
        "rights-only", "bnf-catalogue", "ebooksgratuits", "gallica",
        "gutenberg", "kobo", "numilog", "paris",
    ]
    gallica = next(x for x in sources if x.provider_id == "gallica")
    assert gallica.identity_scope == "public"
    assert gallica.catalog_search_supported is True
    assert gallica.catalog_search_available is True
    assert gallica.catalog_refresh == "unsupported"
    assert gallica.personal_inventory_supported is False
    assert gallica.acquisition == "separate_explicit"
    assert gallica.schema_version == 1
    # Personal inventory is genuinely distinct from a commercial catalogue.
    kobo = next(x for x in sources if x.provider_id == "kobo")
    assert kobo.identity_scope == "authenticated"
    assert kobo.catalog_search_available is True
    assert kobo.personal_inventory_supported is True
    assert kobo.personal_inventory_available is True
    assert kobo.catalog_refresh == "unsupported"
    assert "numilog-catalog" not in names
    assert "technical" not in names
    assert "disabled" not in names
    # Public catalogue discovery never requires a synthetic public Account.
    assert calls.count(("accounts", "alice")) == 1
    assert all(x == "enabled" for x, *_ in calls if isinstance(x, str)
               and x != "accounts")


def test_identity_scope_applies_to_public_plugin_and_private_account_connections():
    service, _ = _rig()
    rows = catalogue_source_capabilities(service, identity_id="bob")
    names = {row.provider_id for row in rows}
    assert "ebooksgratuits" not in names
    assert "kobo" not in names
    assert "numilog" in names
    numilog = next(row for row in rows if row.provider_id == "numilog")
    assert numilog.catalog_search_supported
    assert not numilog.catalog_search_available
    assert numilog.personal_inventory_supported
    assert not numilog.personal_inventory_available
    assert "rights-only" in names
    assert next(row for row in rows if row.provider_id == "rights-only").personal_inventory_available
    assert catalogue_source_capabilities(service, identity_id="stranger") == ()
    assert catalogue_source_capabilities(service, identity_id="alice:private") == ()


def test_disconnected_provider_is_search_capable_but_not_actually_online():
    service, _ = _rig()
    values = catalogue_source_capabilities(service)
    paris = next(value for value in values if value.provider_id == "paris")
    assert paris.catalog_search_supported is True
    assert paris.catalog_search_available is False
    assert paris.personal_inventory_supported is True
    assert paris.personal_inventory_available is False


def test_search_does_not_authorize_catalogue_snapshot_or_background_acquisition():
    service, calls = _rig()
    service.search = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("inventory must never call a provider search")
    )
    service.synchronize = service.acquire_direct_item = lambda *_a, **_k: (
        _ for _ in ()
    ).throw(AssertionError("inventory must never sync or acquire"))
    refs = CatalogBrokerImpl(service).catalogue_source_capabilities("alice")
    assert len(refs) == 8
    assert all(r.catalog_refresh == "unsupported" for r in refs)
    assert all(r.acquisition == "separate_explicit" for r in refs)
    assert all("credential" not in repr(r) for r in refs)
    with pytest.raises(FrozenInstanceError):
        refs[0].catalog_refresh = "snapshot"


def test_core_library_service_facade_additive_without_legacy_broker_change():
    service, _ = _rig()
    values = LibraryService.catalogue_source_capabilities(service, identity_id="alice")
    assert values == catalogue_source_capabilities(service, identity_id="alice")
    from cifereca.plugin_api.brokers import CatalogBroker
    assert not hasattr(CatalogBroker, "catalogue_source_capabilities")
    assert PublicCatalogueSourceCapability is CatalogueSourceCapability
    assert hasattr(CatalogBrokerImpl, "catalogue_source_capabilities")
    assert isinstance(CatalogBrokerImpl(service), CatalogueSourceInventoryBroker)


def test_uncanonical_registry_entry_and_forged_caps_are_not_published():
    service, _ = _rig()
    service.registry.register(_plugin("identity.token", search=True))
    rows = catalogue_source_capabilities(service)
    assert "identity.token" not in {r.provider_id for r in rows}
    assert "Private reader name" not in repr(rows)
    assert not any("http" in repr(row) or "login" in repr(row)
                   for row in rows)


def test_unknown_or_missing_identity_yields_no_private_projection():
    service, calls = _rig()
    assert catalogue_source_capabilities(service, identity_id="bob/private") == ()
    assert all(x[0] != "accounts" for x in calls)
    service.current_identity = None
    assert catalogue_source_capabilities(service) == ()


def test_account_session_of_other_identity_never_marks_private_source_available():
    service, _ = _rig()
    rows = catalogue_source_capabilities(service, identity_id="bob")
    assert next(r for r in rows if r.provider_id == "numilog").personal_inventory_available is False
    assert next(r for r in rows if r.provider_id == "numilog").catalog_search_available is False


def test_catalogue_inventory_is_json_serializable_without_sensitive_metadata():
    service, _ = _rig()
    rows = catalogue_source_capabilities(service)
    import json
    data = json.dumps([asdict(x) for x in rows])
    for fragment in ("a-kobo", "a-numilog", "b-only", "Private reader", "login",
                     "account_id", "credential_id", "token"):
        assert fragment not in data
